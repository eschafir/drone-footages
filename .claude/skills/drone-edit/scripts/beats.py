#!/usr/bin/env python3
"""beats.py: the music's tempo, beat grid, bar grid and energy curve, so cuts land on the music (numpy only).
Input : a music file (anything ffmpeg decodes)
Output: beats.json  {file, duration, bpm, beats[], bars[], energy[[t, e]], sections[[t, e]]}
  onset  log-magnitude spectral flux (STFT 2048 / hop 512 at 22.05 kHz), half-wave rectified, mean-removed
  tempo  autocorrelation of the onset curve over 60-200 BPM, weighted toward ~120 (a log-normal prior), then
         the octave (x2 / x0.5) whose peak is stronger
  beats  dynamic-programming beat tracker (Ellis 2007): onset strength + a penalty for straying from the period
  bars   every 4th beat, starting at the phase whose beats carry the most onset strength (the downbeat guess)
  energy RMS per 0.5 s, smoothed over 2 s, 0-1. sections: bar starts where the energy jumps by > 0.15 (a build,
         a drop, a breakdown): the natural places for a transition or a pace change
Usage:
  python3 beats.py --music videos/<p>/music/track.mp3 [--out videos/<p>/beats.json] [--bpm 96]
--bpm forces the tempo when the estimate is an octave off (listen: does the grid feel half or double time?).
"""
import argparse, os, subprocess
import numpy as np
import common

ap = argparse.ArgumentParser()
ap.add_argument("--music", required=True)
ap.add_argument("--out")
ap.add_argument("--bpm", type=float, help="force the tempo")
ap.add_argument("--beats-per-bar", type=int, default=4)
args = ap.parse_args()

SR, N, HOP = 22050, 2048, 512
FR = SR / HOP
pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", args.music, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                     capture_output=True, check=True).stdout
y = np.frombuffer(pcm, np.float32).astype(np.float64)
if len(y) < N * 4:
    common.die("music is too short to analyse")
dur = len(y) / SR

win = np.hanning(N)
yc = np.pad(y, N // 2)                                                 # centred frames: frame i is at i * HOP / SR
nfr = 1 + (len(yc) - N) // HOP
idx = np.arange(N)[None, :] + HOP * np.arange(nfr)[:, None]
S = np.log1p(100 * np.abs(np.fft.rfft(yc[idx] * win, axis=1)))
flux = np.maximum(0.0, np.diff(S, axis=0)).mean(axis=1)
flux = np.concatenate([[0.0], flux])
onset = flux - np.convolve(flux, np.ones(16) / 16, mode="same")      # remove the slow trend
onset = np.maximum(onset, 0.0)
onset /= onset.std() + 1e-9

def acf_peak(bpm_lo, bpm_hi):
    oc = onset - onset.mean()
    ac = np.correlate(oc, oc, mode="full")[len(oc) - 1:]
    lags = np.arange(int(FR * 60 / bpm_hi), int(FR * 60 / bpm_lo) + 1)
    bpms = 60 * FR / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    k = int(np.argmax(ac[lags] * prior))
    return bpms[k], ac, lags

if args.bpm:
    bpm = args.bpm
else:
    bpm, ac, lags = acf_peak(60, 200)
    for alt in (bpm * 2, bpm / 2):                                     # octave check on the raw autocorrelation
        lag = int(round(60 * FR / alt))
        if 60 <= alt <= 200 and lag < len(ac) and ac[lag] > 1.1 * ac[int(round(60 * FR / bpm))]:
            bpm = alt
period = 60 * FR / bpm

# dynamic programming beat tracking
tight = 100.0
score = onset.copy()
back = np.full(len(onset), -1)
lo, hi = int(round(period / 2)), int(round(period * 2))
for i in range(len(onset)):
    a, b = i - hi, i - lo
    if b < 0:
        continue
    cand = np.arange(max(0, a), b + 1)
    pen = -tight * np.log((i - cand) / period) ** 2
    j = int(np.argmax(score[cand] + pen))
    score[i] = onset[i] + score[cand][j] + pen[j]
    back[i] = cand[j]
end = int(np.argmax(score[-int(period * 2):])) + len(score) - int(period * 2)
beats = []
while end >= 0:
    beats.append(end)
    end = back[end]
beats = np.array(beats[::-1])
bt = beats / FR
# refine: least-squares line through the beats removes frame-quantisation jitter while keeping drift
if len(bt) > 8:
    k = np.arange(len(bt)); a, b = np.polyfit(k, bt, 1)
    fit = a * k + b
    bt = np.where(np.abs(bt - fit) < 0.035, fit, bt)
    bpm = 60.0 / a

bpb = args.beats_per_bar
strength = [onset[beats[p::bpb]].sum() for p in range(min(bpb, len(beats)))]
phase = int(np.argmax(strength)) if strength else 0
bars = bt[phase::bpb]

hop_e = int(SR * 0.5)
rms = np.array([np.sqrt(np.mean(y[i:i + hop_e] ** 2)) for i in range(0, len(y) - hop_e + 1, hop_e)])
sm = np.convolve(np.pad(rms, 2, mode="edge"), np.ones(4) / 4, mode="same")[2:-2]
e = sm / (sm.max() + 1e-9)                                             # relative to the loudest part, not min-max
energy = [[round(i * 0.5, 2), round(float(v), 3)] for i, v in enumerate(e)]

def e_mean(a, b):
    i, j = max(0, int(a / 0.5)), min(len(rms), int(np.ceil(b / 0.5)))
    return float(rms[i:j].mean() / (rms.max() + 1e-9)) if j > i else 0.0
bar_len = bpb * 60 / bpm
delta = [e_mean(t, t + bar_len) - e_mean(t - bar_len, t) if bar_len <= t <= dur - bar_len else 0.0 for t in bars]
sections = []
for i, d in enumerate(delta):                                          # the biggest change within two bars either side
    if abs(d) > 0.15 and abs(d) == max(abs(x) for x in delta[max(0, i - 2):i + 3]):
        sections.append([round(float(bars[i]), 3), round(d, 3)])

out = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(args.music))), "beats.json")
common.save(out, {"file": os.path.abspath(args.music), "duration": round(dur, 3), "bpm": round(float(bpm), 2),
                  "beats": [round(float(t), 3) for t in bt], "bars": [round(float(t), 3) for t in bars],
                  "energy": energy, "sections": sections})
print("%.1f BPM, %d beats, %d bars, %d energy changes, %.1f s -> %s" % (bpm, len(bt), len(bars), len(sections), dur, out))
