"""Local, best-effort speaker labels for short candidate transcripts."""

from __future__ import annotations

import wave
from pathlib import Path


def _utterances(words: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in sorted(words, key=lambda item: (float(item["start"]), float(item["end"]))):
        gap = float(word["start"]) - float(current[-1]["end"]) if current else 0.0
        duration = float(current[-1]["end"]) - float(current[0]["start"]) if current else 0.0
        if current and (gap >= 0.72 or duration >= 5.5):
            groups.append(current)
            current = []
        current.append(dict(word))
    if current:
        groups.append(current)
    return groups


def _read_wav(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        sample_rate = handle.getframerate()
        channels = handle.getnchannels()
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype("float32")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples / 32768.0, sample_rate


def _voice_feature(samples, sample_rate: int, start: float, end: float):
    import librosa
    import numpy as np

    left = max(0, int((start - 0.08) * sample_rate))
    right = min(len(samples), int((end + 0.08) * sample_rate))
    clip = samples[left:right]
    if len(clip) < int(sample_rate * 0.35):
        return None
    mfcc = librosa.feature.mfcc(y=clip, sr=sample_rate, n_mfcc=16)
    delta = librosa.feature.delta(mfcc)
    return np.concatenate((mfcc.mean(axis=1), mfcc.std(axis=1), delta.mean(axis=1)))


def label_words(
    words: list[dict],
    wav_path: Path,
    fixed_label: str | None = None,
    person_start: int = 1,
) -> tuple[list[dict], int]:
    """Label words by a fixed mic identity or local acoustic clusters."""
    groups = _utterances(words)
    if not groups:
        return [], person_start
    if fixed_label:
        return [dict(word, speaker=fixed_label) for group in groups for word in group], person_start

    labels = [0] * len(groups)
    try:
        import numpy as np
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score
        from sklearn.preprocessing import StandardScaler

        samples, sample_rate = _read_wav(wav_path)
        usable: list[int] = []
        features = []
        for index, group in enumerate(groups):
            feature = _voice_feature(
                samples, sample_rate, float(group[0]["start"]), float(group[-1]["end"])
            )
            if feature is not None:
                usable.append(index)
                features.append(feature)
        if len(features) >= 4:
            matrix = StandardScaler().fit_transform(np.asarray(features))
            best = None
            for count in range(2, min(4, len(features) - 1) + 1):
                proposed = KMeans(n_clusters=count, random_state=7, n_init=10).fit_predict(matrix)
                score = silhouette_score(matrix, proposed)
                if best is None or score > best[0]:
                    best = (score, proposed)
            if best and best[0] >= 0.18:
                for index, cluster in zip(usable, best[1]):
                    labels[index] = int(cluster)
    except Exception:
        labels = [0] * len(groups)

    cluster_numbers: dict[int, int] = {}
    next_person = person_start
    result: list[dict] = []
    for group, cluster in zip(groups, labels):
        if cluster not in cluster_numbers:
            cluster_numbers[cluster] = next_person
            next_person += 1
        speaker = f"Person {cluster_numbers[cluster]}"
        result.extend(dict(word, speaker=speaker) for word in group)
    return result, next_person
