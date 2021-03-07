from math import fabs, log, floor, ceil
import torch

# (multiple of 32 so that it best preserves the ratio x 512)
def resize_multiple_32_times_512_at_bigger_axis(orig_dimensions_h_w):

    (h, w) = orig_dimensions_h_w

    if w < 512 or w > h:
        raise Exception("Unexpected - not implemented")

    ratio = float(w) / h # be aware it's python 2!!!
    ideal_width = 512 * ratio
    multiple_32 = ideal_width / 32 # float

    ceil_score = abs(log(multiple_32) - log(ceil(multiple_32)))
    floor_score = abs(log(multiple_32) - log(floor(multiple_32)))

    if ceil_score < floor_score:
        ret = (512, int(ceil(multiple_32)) * 32)
    else:
        ret = (512, int(floor(multiple_32)) * 32)

    return ret


def upsample_bilinear(depth_data, height, width):
    upsampling = torch.nn.Upsample(size=(height, width), mode='bilinear')
    depth_data = upsampling(depth_data)
    return depth_data


def upsample_nearest_numpy(data, height, width):
    data = torch.from_numpy(data)
    data = data.view(1, 1, data.shape[0], data.shape[1])
    #data = upsample_bilinear(data, img.shape[0], img.shape[1])
    upsampling = torch.nn.Upsample(size=(height, width), mode='nearest')
    data = upsampling(data)
    return data.squeeze(dim=0).squeeze(dim=0).numpy()
