"""
Neural style transfer
"""

import gc
from pathlib import Path
from typing import List, Union

import torch
from PIL import Image
from torch import Tensor
from tqdm import tqdm

from maua.ops.image import match_histogram, resample
from maua.ops.loss import tv_loss
from maua.ops.tensor import load_images, tensor2img
from maua.optimizers import load_optimizer, OPTIMIZERS
from maua.parameterizations import load_parameterization
from maua.perceptors import load_perceptor


@torch.no_grad()
def transfer(
    content_img: Union[Tensor, Image.Image, str],
    style_imgs: List[Union[Tensor, Image.Image, str]],
    init_img: Union[Tensor, Image.Image, str] = None,
    init_type="content",
    match_hist="avg",
    size=512,
    parameterization="rgb",
    perceptor="kbc-vgg19",
    perceptor_kwargs={},
    optimizer="LBFGS",
    lr=0.5,
    optimizer_kwargs={},
    n_iters=512,
    content_weight=1,
    style_weight=50,
    tv_weight=100,
    style_scale=1,
    device=torch.device("cuda" if torch.cuda.is_available() else "cpu"),
):
    """Perform a neural style transfer

    Args:
        content_img (Union[Tensor, Image.Image, str]): Image whose structure will be preserved in output
        style_imgs (List[Union[Tensor, Image.Image, str]]): Images whose style will be apparent in output
        init_img (Union[Tensor, Image.Image, str], optional): Image to initialize optimization with.
        init_type (str, optional): How to initialize the image for optimization. Choices ['content', 'random', 'init_img'].
        match_hist (str, optional): How to match color histogram of intermediate images. Choices ['avg', False].
        size (int, optional): Size of output image.
        parameterization (str, optional): How to parameterize the image. Choices ["rgb", "vqgan"]
        perceptor (str, optional): Which perceptor to optimize with. Choices ["kbc-vgg19", "pgg-vgg19", "pgg-vgg16", "pgg-prune", "pgg-nyud", "pgg-fcn32s", "pgg-sod", "pgg-nin"].
        perceptor_kwargs (dict, optional): Key word arguments for the Perceptor class.
        optimizer (str, optional): Optimizer to use. For choices see optimizers.py
        lr (float, optional): Optimizer learning rate.
        optimizer_kwargs (dict, optional): Key word arguments for the optimizer.
        n_iters (int, optional): Number of iterations to optimize for.
        content_weight (int, optional): Strength of content preserving loss. Higher values will lead to outputs which better preserve the content's structure and texture.
        style_weight (int, optional): Strength of style loss. Higher values will lead to outputs which look more like the style images.
        tv_weight (int, optional): Strength of total variation loss. Higher values lead to smoother outputs.
        style_scale (int, optional): Scale of style images relative to output image. Larger scales will make textures from styles larger in the output image.
        content_layers (List[int], optional): Layers in Perceptor network that the content loss will be calculated for. Defaults to None which uses defaults defined in each Perceptor class.
        style_layers (List[int], optional): Layers in Perceptor network that the style loss will be calculated for. Defaults to None which uses defaults defined in each Perceptor class.
        device (torch.device, optional): Device to run on.

    Returns:
        Tensor: Result image
    """
    content_img, style_imgs, init_img = load_images(content_img, style_imgs, init_img)

    content_img = resample(content_img.to(device), size)
    style_imgs = [resample(im.to(device), size * style_scale) for im in style_imgs]
    content_img = match_histogram(content_img, style_imgs, mode=match_hist)

    if init_img is not None:
        init_tensor = init_img
    elif init_type == "content":
        init_tensor = content_img
    elif init_type == "random":
        init_tensor = None

    pastiche = load_parameterization(parameterization)(
        content_img.shape[2], content_img.shape[3], tensor=init_tensor
    ).to(device)

    perceptor = load_perceptor(perceptor)(
        content_strength=content_weight, style_strength=style_weight, **perceptor_kwargs
    ).to(device)
    target_embeddings = perceptor.get_target_embeddings(content_img, style_imgs)

    del content_img, style_imgs
    gc.collect()
    torch.cuda.empty_cache()

    with torch.enable_grad(), tqdm(total=n_iters, desc=f"Optimizing @ {size}px") as pbar:

        opt, niter = load_optimizer(optimizer, lr, optimizer_kwargs, n_iters, pastiche.parameters())

        def closure():
            opt.zero_grad()
            pastiche.update_ema()

            img = pastiche()

            loss = perceptor.get_loss(img, target_embeddings)
            if tv_weight > 0:
                loss += tv_weight * tv_loss(img)

            loss.backward()
            pbar.update()
            return loss

        for _ in range(niter):
            opt.step(closure)

    return pastiche.decode_average()


def argument_parser():
    import argparse

    # fmt: off
    parser = argparse.ArgumentParser(description=transfer.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--content")
    parser.add_argument("--styles", nargs="+")
    parser.add_argument("--init_img", type=str, default=None)
    parser.add_argument("--init_type", default="content", choices=['content', 'random', 'init_img'])
    parser.add_argument("--match_hist", default="avg", choices=['avg', 'False'])
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--parameterization", default="rgb", choices=["rgb", "vqgan"])
    parser.add_argument("--perceptor", default="kbc-vgg19", choices=["kbc-vgg19" ,"pgg-vgg19", "pgg-vgg16", "pgg-prune", "pgg-nyud", "pgg-fcn32s", "pgg-sod", "pgg-nin"])
    parser.add_argument("--perceptor_kwargs", nargs="*", default=[])
    parser.add_argument("--optimizer", default="LBFGS", choices=OPTIMIZERS)
    parser.add_argument("--lr", type=float, default=0.5)
    parser.add_argument("--optimizer_kwargs", nargs="*", default=[])
    parser.add_argument("--n_iters", type=int, default=512)
    parser.add_argument("--content_weight", type=float, default=1)
    parser.add_argument("--style_weight", type=float, default=50)
    parser.add_argument("--tv_weight", type=float, default=100)
    parser.add_argument("--style_scale", type=float, default=1)
    parser.add_argument("--device", default=torch.device("cuda" if torch.cuda.is_available() else "cpu"))
    # fmt: on
    return parser


def main(args):
    if len(args.perceptor_kwargs) > 0:
        perceptor_kwargs = {
            k: eval(t)(v)
            for k, t, v in zip(args.perceptor_kwargs[::3], args.perceptor_kwargs[1::3], args.perceptor_kwargs[2::3])
        }
    else:
        perceptor_kwargs = {}

    if len(args.optimizer_kwargs) > 0:
        optimizer_kwargs = {
            k: eval(t)(v)
            for k, t, v in zip(args.optimizer_kwargs[::3], args.optimizer_kwargs[1::3], args.optimizer_kwargs[2::3])
        }
    else:
        optimizer_kwargs = {}

    img = transfer(
        content_img=args.content,
        style_imgs=args.styles,
        init_img=args.init_img,
        init_type=args.init_type,
        match_hist=args.match_hist,
        size=args.size,
        parameterization=args.parameterization,
        perceptor=args.perceptor,
        perceptor_kwargs=perceptor_kwargs,
        optimizer=args.optimizer,
        lr=args.lr,
        optimizer_kwargs=optimizer_kwargs,
        n_iters=args.n_iters,
        content_weight=args.content_weight,
        style_weight=args.style_weight,
        tv_weight=args.tv_weight,
        style_scale=args.style_scale,
        device=args.device,
    )
    tensor2img(img).save(f"output/{'_'.join([Path(arg).stem for arg in [args.content] + args.styles])}.png")


if __name__ == "__main__":
    args = argument_parser().parse_args()
    main(args)
