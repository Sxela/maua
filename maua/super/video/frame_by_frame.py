import argparse
import os
from pathlib import Path

import numpy as np
import torch
from decord import VideoReader
from tqdm import tqdm

from maua.ops.video import VideoWriter
from maua.super.image import MODEL_NAMES
from maua.super.image import upscale as upscale_images


def upscale(video_file, model_name, device, out_dir):
    vr = VideoReader(video_file)
    fps = vr.get_avg_fps()
    h, w, _ = vr[0].shape

    out_file = f"{out_dir}/{Path(video_file).stem}_{model_name}.mp4"
    with VideoWriter(output_file=out_file, output_size=(4 * w, 4 * h), fps=fps) as video:
        frames = (torch.from_numpy(vr[i].asnumpy()).permute(2, 0, 1).unsqueeze(0).div(255) for i in range(len(vr)))
        for frame in tqdm(upscale_images(frames, model_name, device), total=len(vr)):
            video.write(frame)

    return VideoReader(out_file)


def main(args):
    for video_file in args.video_files:

        out_file = f"{args.out_dir}/{Path(video_file).stem}_{args.model_name}.mp4"
        if os.path.exists(out_file):
            print(f"Skipping {Path(video_file).stem}, output {Path(out_file).stem} already exists!")
            continue

        upscale(video_file, args.model_name, args.device, args.out_dir)


def argument_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("video_files", nargs="+")
    parser.add_argument("--model_name", default="latent-diffusion", choices=MODEL_NAMES)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--out_dir", default="output/")
    return parser
