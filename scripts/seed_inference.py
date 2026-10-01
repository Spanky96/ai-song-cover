"""Run the pinned Seed-VC CLI with Apple MPS pitch and vocoder compatibility."""

import os
import runpy
import sys
from pathlib import Path


def main():
    repo = Path(sys.argv[1]).resolve()
    script = repo / "inference.py"
    sys.path.insert(0, str(repo))
    os.environ["HF_HUB_CACHE"] = str(repo / "checkpoints/hf_cache")

    import numpy as np
    import torchaudio
    from modules.bigvgan.bigvgan import BigVGAN
    from modules.rmvpe import RMVPE

    infer_pitch = RMVPE.infer_from_audio

    def infer_float32(self, *args, **kwargs):
        return np.asarray(infer_pitch(self, *args, **kwargs), dtype=np.float32)

    RMVPE.infer_from_audio = infer_float32
    vocoder_forward = BigVGAN.forward

    def compatible_vocoder(self, inputs):
        if inputs.device.type == "mps":
            if next(self.parameters()).device.type == "mps":
                print("BigVGAN: CPU vocoder for the MPS convolution size limit", flush=True)
                self.cpu()
            return vocoder_forward(self, inputs.cpu()).to(inputs.device)
        return vocoder_forward(self, inputs)

    BigVGAN.forward = compatible_vocoder
    save_audio = torchaudio.save

    def save_float_vocal(path, waveform, sample_rate, **kwargs):
        # Keep the converted stem in float format for later gain and mixing.
        kwargs.update(encoding="PCM_F", bits_per_sample=32)
        return save_audio(path, waveform, sample_rate, **kwargs)

    torchaudio.save = save_float_vocal
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
