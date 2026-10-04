"""Build the <=60 s submission cuts from the 120 s masters.

Usage:
  python docs/media/build_60s.py --vo DIR      # DIR holds d1..d6.wav, t1..t8.wav

Narration WAVs come from Kokoro TTS (kokoro-onnx, voice am_michael, speed 1.08)
reading the lines in NARRATION below; see docs/submission-scripts-60s.md.
Each segment re-times one scene of the master (trim plus optional speed-up) to
fit its narration line. Visuals, captions and grades are unchanged from the
masters; nothing is re-simulated. Needs ffmpeg with libass.
"""
import argparse
import re
import subprocess
import tempfile
import wave
from pathlib import Path

MEDIA = Path(__file__).resolve().parent
PAD = 0.25  # silence before each narration line

NARRATION = {
    "d1": "Robots assume Earth's physics. Sol Zero drops one into a simulated world with an unknown force law. Its Earth-physics first shot misses.",
    "d2": "So an AI lab of specialist agents takes over. It gets twelve experiments, and it chooses every one itself.",
    "d3": "Before each measurement, it pre-registers predictions for every rival law. One predicted two point three six metres. The robot measured zero point five six. Rejected.",
    "d4": "Evidence changed the plan six times. After ten experiments, the lab decided it knew enough, and committed.",
    "d5": "One firing table. Five untouched targets, one shot each. The server graded five hits out of five, including three beyond the range it ever tested.",
    "d6": "Every prediction, measurement and decision is in an auditable ledger. One recorded development run, replayed in simulation.",
    "t1": "The world stays hidden. Omnigent agents reach it only through a public HTTP API, and scoring stays with the host.",
    "t2": "Each role owns one decision. The Theorist fits laws, the Experimentalist ranks tests, the Operator executes, the Analyst judges, and the principal investigator decides when to stop.",
    "t3": "Predictions must cover every live law before a run. The server enforces the twelve-experiment budget.",
    "t4": "Each verdict feeds the next choice of experiment. Laws, predictions, results, verdicts and decision diffs land in a durable ledger.",
    "t6": "Today's Operator runs deterministically, with no model turn. The recorded baseline predates that.",
    "t7": "Shared planning tools produce one five-shot firing table. The server graded it: five of five hits.",
    "t8": "A compressed replay of a development simulation. The evidence is in the repo.",
}

# (narration key, source start s, source end s, segment tail after narration s)
# The source window is sped up uniformly to fill PAD + narration + tail.
CUTS = {
    "demo": [
        ("d1", 3.0, 18.0, 0.35),     # title, Earth-physics shot zero, miss
        ("d2", 21.0, 33.0, 0.35),    # roles; experiments 1-2 begin
        ("d3", 42.0, 56.0, 0.35),    # experiment 2: predicted 2.36 m, measured 0.563 m
        ("d4", 65.7, 80.0, 0.35),    # evidence changes the next test; experiments to 10
        ("d5", 82.0, 100.0, 0.35),   # five mission shots, server-graded hits, claim card
        ("d6", 100.0, 108.92, 0.35), # predictive-only claim, limitations
    ],
    "technical": [
        ("t1", 0.0, 8.9, 0.2),       # agents see measurements, not the world
        ("t2", 15.0, 30.0, 0.2),     # roles and handoffs over experiment replay
        ("t3", 30.0, 37.08, 0.2),    # server enforces the contract
        ("t4", 45.0, 60.0, 0.2),     # ledger over experiments 4-6
        ("t6", 75.0, 82.08, 0.2),    # current Operator uses no model turn
        ("t7", 90.5, 104.5, 0.2),    # five mission shots with server grades
        ("t8", 105.0, 110.29, 0.2),  # what this demonstrates
    ],
}


def run(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def wav_seconds(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def ts(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def chunks(text):
    """Split narration into caption-sized pieces (sentences, then commas)."""
    out = []
    for sent in re.split(r"(?<=[.:])\s+", text):
        while len(sent) > 78 and "," in sent[:78]:
            cut = sent[:78].rfind(",") + 1
            out.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        out.append(sent)
    return out


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,34,&H00F5F5F5,&H00F5F5F5,&H00211109,&HC0211109,0,0,0,0,100,100,0,0,3,10,0,2,80,80,168,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build(name, vo, work):
    segs, audio_inputs, delays, ass, srt = [], [], [], [ASS_HEAD], []
    t = 0.0
    for i, (key, a, b, tail) in enumerate(CUTS[name]):
        wav = vo / f"{key}.wav"
        speech = wav_seconds(wav)
        length = PAD + speech + tail
        speed = (b - a) / length
        seg = work / f"{name}_{i}.mp4"
        vf = f"setpts=(PTS-STARTPTS)/{speed:.6f},fps=30,trim=duration={length:.4f}"
        if name == "technical":  # scene 05 is dropped; hide the "TECHNICAL / 0n" counter
            vf += ",drawbox=x=1440:y=35:w=300:h=50:color=0x091121:t=fill"
        run("-ss", a, "-t", b - a, "-i", MEDIA / f"{name}.mp4", "-an", "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", seg)
        segs.append(seg)
        audio_inputs += ["-i", wav]
        delays.append(t + PAD)
        pieces = chunks(NARRATION[key])
        total = sum(len(p) for p in pieces)
        c = t + PAD
        for p in pieces:
            d = speech * len(p) / total
            ass.append(f"Dialogue: 0,{ts(c)},{ts(c + d)},Cap,,0,0,0,,{p}\n")
            srt.append((c, c + d, p))
            c += d
        t += length

    concat = work / f"{name}_list.txt"
    concat.write_text("".join(f"file '{s}'\n" for s in segs))
    joined = work / f"{name}_joined.mp4"
    run("-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", joined)

    (work / f"{name}.ass").write_text("".join(ass))
    n = len(delays)
    mix = "".join(
        f"[{i + 1}:a]aresample=48000,adelay={int(d * 1000)}:all=1[a{i}];" for i, d in enumerate(delays)
    ) + "".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad,atrim=duration={t:.3f},afade=t=out:st={t - 0.4:.3f}:d=0.4,aformat=channel_layouts=stereo[aout]"
    vf = f"ass={work / (name + '.ass')},fade=t=in:st=0:d=0.3,fade=t=out:st={t - 0.5:.3f}:d=0.5[vout]"
    out = MEDIA / f"{name}_60s.mp4"
    run("-i", joined, *audio_inputs, "-filter_complex", f"[0:v]{vf};{mix}",
        "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        "-t", f"{t:.3f}", out)

    def srt_ts(x):
        ms = round(x * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    (MEDIA / f"{name}_60s.srt").write_text(
        "".join(f"{i}\n{srt_ts(s)} --> {srt_ts(e)}\n{p}\n\n" for i, (s, e, p) in enumerate(srt, 1))
    )
    print(f"{out.name}: planned {t:.2f} s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vo", type=Path, required=True)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        for name in CUTS:
            build(name, args.vo, Path(tmp))
