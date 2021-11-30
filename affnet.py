from dataclasses import dataclass
import math
from matplotlib.patches import Circle
from matplotlib.collections import PatchCollection

import pickle
import os
import cv2 as cv
import kornia as KR
import kornia.feature as KF
import matplotlib.pyplot as plt
import numpy as np
import torch
import argparse
from pipeline import Pipeline
from kornia_moons.feature import *
from config import CartesianConfig
from hard_net_descriptor import HardNetDescriptor
from utils import Timer
from rectification import get_rotation_matrix

# def get_local_descriptors(img, cv2_sift_kpts, kornia_descriptor):
#     # We will not train anything, so let's save time and memory by no_grad()
#     with torch.no_grad():
#         kornia_descriptor.eval()
#         timg = K.color.rgb_to_grayscale(KR.image_to_tensor(img, False).float()) / 255.
#         lafs = laf_from_opencv_SIFT_kpts(cv2_sift_kpts)
#         # We will estimate affine shape of the feature and re-orient the keypoints with the OriNet
#         affine = KF.LAFAffNetShapeEstimator(True)
#         orienter = KF.LAFOrienter(32, angle_detector=KF.OriNet(True))
#         orienter.eval()
#         affine.eval()
#         lafs2 = affine(lafs, timg)
#         lafs_new = orienter(lafs2, timg)
#         patches = KF.extract_patches_from_pyramid(timg, lafs_new, 32)
#         B, N, CH, H, W = patches.size()
#         # Descriptor accepts standard tensor [B, CH, H, W], while patches are [B, N, CH, H, W] shape
#         # So we need to reshape a bit :)
#         descs = kornia_descriptor(patches.view(B * N, CH, H, W)).view(B * N, -1)
#     return descs.detach().cpu().numpy(), lafs2
#
#
# def sift_korniadesc_matching(fname1, fname2, descriptor):
#     img1 = cv.cvtColor(cv.imread(fname1), cv.COLOR_BGR2RGB)
#     img2 = cv.cvtColor(cv.imread(fname2), cv.COLOR_BGR2RGB)
#
#     sift = cv.SIFT_create(8000)
#     kps1 = sift.detect(img1, None)
#     kps2 = sift.detect(img2, None)
#     # That is the only change in the pipeline -- descriptors
#     descs1, lafs_new1 = get_local_descriptors(img1, kps1, descriptor)
#     descs2, lafs_new2 = get_local_descriptors(img2, kps2, descriptor)
#     # The rest is the same, as for SIFT
#
#     dists, idxs = KF.match_smnn(torch.from_numpy(descs1), torch.from_numpy(descs2), 0.95)
#     tentatives = cv2_matches_from_kornia(dists, idxs)
#     src_pts = np.float32([kps1[m.queryIdx].pt for m in tentatives]).reshape(-1, 2)
#     dst_pts = np.float32([kps2[m.trainIdx].pt for m in tentatives]).reshape(-1, 2)
#     F, inliers_mask = pydegensac.findFundamentalMatrix(src_pts, dst_pts, 0.75, 0.99, 100000)
#     inliers_mask = 1 * inliers_mask
#     draw_params = dict(matchColor=(255, 255, 0),  # draw matches in yellow color
#                        singlePointColor=None,
#                        matchesMask=inliers_mask.ravel().tolist(),  # draw only inliers
#                        flags=2)
#     img_out = cv.drawMatches(img1, kps1, img2, kps2, tentatives, None, **draw_params)
#     plt.figure(figsize=(15, 15))
#     #fig, ax = plt.subplots(figsize=(15, 15))
#     plt.imshow(img_out, interpolation='nearest')
#     plt.show()
#     print(f'{inliers_mask.sum()} inliers found')
#     return lafs_new1, lafs_new2
#
#
# def orig_main():
#
#     fname1 = 'kn_church-2.jpg'
#     fname2 = 'kn_church-8.jpg'
#
#     hardnet = KF.HardNet(True)
#     lafs1, lafs2 = sift_korniadesc_matching(fname1, fname2, hardnet)
#
#     img1 = cv.cvtColor(cv.imread(fname1), cv.COLOR_BGR2RGB)
#     timg1 = KR.image_to_tensor(img1, False).float() / 255.
#
#     # Let's visualize some of the local features
#     visualize_LAF(timg1, lafs1[:, 1200:1500], figsize=(8, 12))
#
#     scale1 = KF.get_laf_scale(lafs1)
#     lafs_no_scale = KF.scale_laf(lafs1, 1. / scale1)
#
#     u, s, v = torch.linalg.svd(lafs_no_scale[0:, :, :2, :2].reshape(-1, 4).t())
#     aff1 = u[0].reshape(2, 2)
#     aff_full = torch.cat([aff1, torch.tensor([800, 400]).view(2, 1)], dim=1)[None]
#
#     aff_full = torch.cat([aff1, torch.tensor([100, 100]).view(2, 1)], dim=1)[None]
#     img1_warped = KR.geometry.rotate(KR.geometry.warp_affine(timg1, aff_full, dsize=(1000, 800)),
#                                     torch.tensor(-90.))
#     plt.figure(figsize=(6, 8))
#     plt.imshow(KR.tensor_to_image(img1_warped[0, :, 100:600, 350:])[:, ::-1])
#     plt.show()


# def normals_caching_scheme():
#     cache_normals_fn = "work/normals.pt"
#     if os.path.exists(cache_normals_fn):
#         Timer.start_check_point("normals cache read")
#         with open(cache_normals_fn, "rb") as f:
#             normals_at_kps = pickle.load(f)
#         Timer.end_check_point("normals cache read")
#     else:
#         Timer.start_check_point("normals computation")
#         normals = pipeline.process_image(img_name, idx % 2)[1]
#         normals_at_kps = get_kpts_normals(normals, laffs_no_scale).unsqueeze(dim=0)
#         Timer.end_check_point("normals computation")
#         Timer.start_check_point("normals cache save")
#         with open(cache_normals_fn, "wb") as f:
#             pickle.dump(normals_at_kps, f)
#         Timer.end_check_point("normals cache save")


def get_rotation_matrices(unit_rotation_vectors, thetas):

    # Rodrigues formula
    # R = I + sin(theta) . K + (1 - cos(theta)).K**2

    K = torch.zeros([*unit_rotation_vectors.shape[:2], *[3, 3]])

    # K[:, :, 0, 0] = 0.0
    K[:, :, 0, 1] = -unit_rotation_vectors[:, :, 2]
    K[:, :, 0, 2] = unit_rotation_vectors[:, :, 1]
    K[:, :, 1, 0] = unit_rotation_vectors[:, :, 2]
    # K[:, :, 1, 1] = 0.0
    K[:, :, 1, 2] = -unit_rotation_vectors[:, :, 0]

    K[:, :, 2, 0] = -unit_rotation_vectors[:, :, 1]
    K[:, :, 2, 1] = unit_rotation_vectors[:, :, 0]
    # K[:, :, 2, 2] = 0.0

    a = torch.eye(3)[None, None]

    b = torch.sin(thetas).unsqueeze(3) * K

    c = (1.0 - torch.cos(thetas)).unsqueeze(3) * K @ K

    R = a + b + c

    a0 = torch.eye(3)
    b0 = torch.sin(thetas[0, 0]) * K[0, 0]
    c0 = (1.0 - torch.cos(thetas[0, 0])) * K[0, 0] @ K[0, 0]
    R0 = a0 + b0 + c0

    R1 = get_rotation_matrix(unit_rotation_vectors[0, 0], thetas[0, 0])

    assert torch.all(R[0,0] == R0)
    assert torch.allclose(R[0,0], torch.from_numpy(R1).to(torch.float))

    return R


def get_rectification_rotations(normals):

    # now the normals will be "from" me, "inside" the surfaces
    normals = -torch.from_numpy(normals)[None]

    z = torch.tensor([[[0.0, 0.0, 1.0]]]).expand(-1, normals.shape[1], -1)
    print()

    # this handles the case when there is only one dominating plane

    assert torch.all(normals[..., 2] > 0)

    rotation_vectors = torch.cross(normals, z)
    rotation_vector_norms = torch.norm(rotation_vectors, dim=2).unsqueeze(2)
    unit_rotation_vectors = rotation_vectors / rotation_vector_norms

    Rs = get_rotation_matrices(unit_rotation_vectors, rotation_vector_norms)
    assert torch.allclose(torch.det(Rs), torch.tensor(1.0))
    return Rs


def get_rectification_homographies(normals, K):
    Rs = get_rectification_rotations(normals)
    K = torch.from_numpy(K).to(torch.float)[None, None]
    K_inv = torch.inverse(K).to(torch.float)
    Hs = K @ Rs @ K_inv
    return Hs


def get_lafs(file_path, descriptor, img_name):

    img = cv.cvtColor(cv.imread(file_path), cv.COLOR_BGR2RGB)
    plt.figure(figsize=(9, 9))
    plt.title(img_name)
    plt.imshow(img)
    plt.close()

    kps, descs, laffs = descriptor.detectAndCompute(img, give_laffs=True, filter=hard_net_filter)

    timg = KR.image_to_tensor(img, False).float() / 255.

    # Let's visualize some of the local features
    visualize_LAF(timg, laffs[:, ::10], figsize=(8, 12))

    scale1 = KF.get_laf_scale(laffs)
    lafs_no_scale = KF.scale_laf(laffs, 1. / scale1)

    # u, s, v = torch.linalg.svd(lafs_no_scale[0:, :, :2, :2].reshape(-1, 4).t())
    # aff1 = u[0].reshape(2, 2)
    # aff_full = torch.cat([aff1, torch.tensor([800, 400]).view(2, 1)], dim=1)[None]
    #
    # aff_full = torch.cat([aff1, torch.tensor([100, 100]).view(2, 1)], dim=1)[None]
    # img1_warped = KR.geometry.rotate(KR.geometry.warp_affine(timg1, aff_full, dsize=(1000, 800)),
    #                                 torch.tensor(-90.))
    # plt.figure(figsize=(6, 8))
    # plt.imshow(KR.tensor_to_image(img1_warped[0, :, 100:600, 350:])[:, ::-1])
    # plt.show()

    return kps, descs, lafs_no_scale


def prepare_pipeline():

    Timer.start_check_point("prepare_pipeline")
    parser = argparse.ArgumentParser(prog='pipeline')
    parser.add_argument('--output_dir', help='output dir')
    args = parser.parse_args()

    pipeline, config_map = Pipeline.configure("config.txt", args)
    all_configs = CartesianConfig.get_configs(config_map)
    config, cache_map = all_configs[0]
    pipeline.config = config
    pipeline.cache_map = cache_map

    pipeline.start()
    Timer.end_check_point("prepare_pipeline")

    return pipeline


# def get_kpts_normals(normals, laffs_no_scale):
#
#     Timer.start_check_point("get_kpts_normals")
#
#     coords = laffs_no_scale[0, :, :, 2]
#     coords = torch.round(coords)
#     torch.clamp(coords[:, 0], 0, normals.shape[1] - 1, out=coords[:, 0])
#     torch.clamp(coords[:, 1], 0, normals.shape[0] - 1, out=coords[:, 1])
#     coords = coords.to(torch.long)
#
#     normals_kpts = normals[coords[:, 1], coords[:, 0]]
#
#     Timer.end_check_point("get_kpts_normals")
#
#     return normals_kpts
#
#


# NOTE : basically version of utils.get_kpts_normals, but for torch!!!
def get_kpts_normals_indices(components_indices, valid_components_dict, laffs_no_scale):

    Timer.start_check_point("get_kpts_normals_indices")

    coords = laffs_no_scale[0, :, :, 2]
    coords = torch.round(coords)
    torch.clamp(coords[:, 0], 0, components_indices.shape[1] - 1, out=coords[:, 0])
    torch.clamp(coords[:, 1], 0, components_indices.shape[0] - 1, out=coords[:, 1])
    coords = coords.to(torch.long)

    component_indices = components_indices[coords[:, 1], coords[:, 0]]
    normals_indices = torch.ones_like(coords[:, 0]) * -1
    for valid_component in valid_components_dict:
        normals_indices[component_indices == valid_component] = valid_components_dict[valid_component]

    Timer.end_check_point("get_kpts_normals_indices")

    return normals_indices[None]


# NOTE : also basically version of utils.get_kpts_normals, but for torch!!!
# TODO there is some duplicate code with get_kpts_normals_indices
def get_kpts_components_indices(components_indices, valid_components_dict, laffs_no_scale):

    Timer.start_check_point("get_kpts_components_indices")

    coords = laffs_no_scale[0, :, :, 2]
    coords = torch.round(coords)
    torch.clamp(coords[:, 0], 0, components_indices.shape[1] - 1, out=coords[:, 0])
    torch.clamp(coords[:, 1], 0, components_indices.shape[0] - 1, out=coords[:, 1])
    coords = coords.to(torch.long)

    component_indices = components_indices[coords[:, 1], coords[:, 0]]
    normals_indices = torch.ones_like(coords[:, 0]) * -1
    for valid_component in valid_components_dict:
        normals_indices[component_indices == valid_component] = valid_component

    Timer.end_check_point("get_kpts_components_indices")

    return normals_indices[None]


def compose_lin_maps(ts, phis, lambdas=None, psis=None):

    #TODO maybe expand?
    if lambdas is None:
        lambdas = 1.0 / torch.sqrt(ts)
        lambdas = lambdas.repeat(1, 1, 1, 1)
    if psis is None:
        psis = torch.zeros_like(phis)

    def get_rotation_matrices(angles):
        R = torch.cos(angles)
        R = R.repeat(1, 1, 2, 2)
        R[:, :, 1, 0] = torch.sin(angles)
        R[:, :, 0, 1] = -R[:, :, 1, 0]
        return R

    R_psis = get_rotation_matrices(psis)
    R_phis = get_rotation_matrices(phis)

    T_ts = torch.zeros_like(ts)
    T_ts = T_ts.repeat(1, 1, 2, 2)
    T_ts[:, :, 0, 0] = ts
    T_ts[:, :, 1, 1] = 1

    lin_maps = lambdas * R_psis @ T_ts @ R_phis
    return lin_maps


def decompose_lin_maps(l_maps, asserts=True, rec=True):

    Timer.start_check_point("decompose_lin_maps")

    l_maps = torch.inverse(l_maps)

    # CONTINUE: think about whether this is correct: features: tilts (t in [1.0, +inf) ) -> that would mean normalizing transformation may be
    # be (t in (0, 1.0] )
    # btw. it's easier to first decompose and then compute the reverse, is it not?
    # to test this, maybe

    # if rec:
    #     l_maps_orig = l_maps
    #     print("rec: {}".format(l_maps[0, 0]))
    #     lambdas2, psis2, ts2, phis2 = decompose_lin_maps(l_maps_orig, asserts, rec=False)
    #     Timer.start_check_point("inverse")
    #     l_maps = torch.inverse(l_maps)
    #
    #     projected_lin_map = compose_lin_maps(ts2[0, 0], phis2[0, 0])
    #     projected_lin_map_inv = torch.inverse(projected_lin_map)
    #     lambdas3, psis3, ts3, phis3 = decompose_lin_maps(projected_lin_map_inv, asserts, rec=False)
    #     lambdas4, psis4, ts4, phis4 = decompose_lin_maps(projected_lin_map, asserts, rec=False)
    #     print()
    #
    #     Timer.end_check_point("inverse")
    #     print("rec2: {}".format(l_maps[0, 0]))
    #     print("rec2 orig: {}".format(l_maps_orig[0, 0]))
    #     print()
    # else:
    #     print("NOT rec: {}".format(l_maps[0, 0]))

    # TODO watch out for CUDA efficiency
    U, s, V = torch.svd(l_maps)
    V = torch.transpose(V, dim0=2, dim1=3)

    if asserts:
        assert torch.all(torch.sgn(s[:, :, 0]) == torch.sgn(s[:, :, 1]))
        assert torch.all(s[:, :, 0] != 0)

    factor = torch.sgn(s[:, :, :1])
    U = factor[:, :, :, None] * U
    s = factor * s
    lambdas = s[:, :, 1].clone()
    s = s / s[:, :, 1:]

    if asserts:
        assert torch.all(s[:, :, 0] >= 1)
        assert torch.all(s[:, :, 1] == 1)

    dets_u = torch.det(U)
    dets_v = torch.det(V)
    if asserts:
        assert torch.allclose(dets_v, dets_u, atol=1e-07)
        assert torch.allclose(torch.abs(dets_v), torch.tensor(1.0), atol=1e-07)

    def swap_rows(A):
        for col in [0, 1]:
            tmp_el = A[:, :, 0, col].clone()
            A[:, :, 0, col] = torch.where(dets_v < 0.0, A[:, :, 1, col], A[:, :, 0, col])
            A[:, :, 1, col] = torch.where(dets_v < 0.0, tmp_el, A[:, :, 1, col])

    swap_rows(U)
    swap_rows(V)

    dets_u = torch.det(U)
    dets_v = torch.det(V)
    if asserts:
        assert torch.allclose(dets_v, dets_u, atol=1e-07)
        assert torch.allclose(dets_v, torch.tensor(1.0), atol=1e-07)

    # phi in (0, pi)
    phi_norm_factor = torch.where(V[:, :, :1, 1:] > 0, -1.0, 1.0)
    V = V * phi_norm_factor
    U = U * phi_norm_factor

    def assert_rotation(A, angle):
        sins_ang = torch.sin(angle)
        if asserts:
            assert torch.allclose(sins_ang, -A[:, :, 0, 1], atol=1e-03)
            assert torch.allclose(sins_ang, A[:, :, 1, 0], atol=1e-03)
            assert torch.allclose(A[:, :, 1, 1], A[:, :, 0, 0], atol=1e-05)

    phis = torch.arccos(torch.clamp(V[:, :, 0, 0], -1.0, 1.0))
    assert_rotation(V, phis)

    psis = torch.arcsin(-torch.clamp(U[:, :, 0, 1], -1.0, 1.0))
    #psis_norm_factor = torch.where(U[:, :, :1, 1:] > 0, -1.0, 1.0)
    #psis = psis * psis_norm_factor[:, :, 0, 0]

    assert_rotation(U, psis)

    ts = s[:, :, 0]

    Timer.end_check_point("decompose_lin_maps")

    # if rec:
    #     # CONTINUE: ts == ts2, therefore to je blbe!!! - viz predchozi CONTINUE!
    #     #lambdas2, psis2, ts2, phis2
    #     print()

    return lambdas, psis, ts, phis


# def get_rectification_rotation_vectors(normals):
#
#     # now the normals will be "from" me, "inside" the surfaces
#     normals = -normals
#
#     z = torch.tensor([0.0, 0.0, 1.0])
#
#     # this handles the case when there is only one dominating plane
#     assert normals[:, 2] > 0
#
#     rotation_vectors = torch.cross(normals, z)
#     rotation_vector_norms = np.linalg.norm(rotation_vectors, axis=1) # sins_theta
#     unit_rotation_vector = rotation_vectors / rotation_vector_norms
#
#
#     # theta = math.asin(abs_sin_theta) * rotation_factor
#     # # TODO !!!
#     # theta = min(theta, math.pi * 4.0/9.0)
#     #
#     # R = get_rotation_matrix(unit_rotation_vector, theta)
#     # det = np.linalg.det(R)
#     # assert math.fabs(det - 1.0) < 0.0001
#     # return R

# def get_normal_vec_from_decomposition(ts, phis):
#     Timer.start_check_point("get_normal_vec_from_decomposition")
#     sin_theta = torch.sqrt(ts ** 2 - 1) / ts
#     xs = torch.cos(phis) * sin_theta
#     ys = torch.sin(phis) * sin_theta
#     zs = torch.sqrt(1 - xs ** 2 - ys ** 2)
#     norms_from_lafs = torch.cat((xs.unsqueeze(2), ys.unsqueeze(2), zs.unsqueeze(2)), 2)
#
#     Timer.end_check_point("get_normal_vec_from_decomposition")
#     return norms_from_lafs


# TODO provide img as a parameter to save some computation
def get_laffs_no_scale_p_cached(img_file_path, img_name, descriptor, cache_laffs):
    cache_laffs_fn = "work/laffs_no_scale.pt"
    if cache_laffs and os.path.exists(cache_laffs_fn):
        Timer.start_check_point("laffs_no_scale cache read")
        laffs_no_scale = torch.load(cache_laffs_fn)
        Timer.end_check_point("laffs_no_scale cache read")
    else:
        Timer.start_check_point("laffs_no_scale computation")
        _, _, laffs_no_scale = get_lafs(img_file_path, descriptor, img_name)
        Timer.end_check_point("laffs_no_scale computation")
        Timer.start_check_point("laffs_no_scale saving")
        torch.save(laffs_no_scale, cache_laffs_fn)
        Timer.end_check_point("laffs_no_scale saving")
    return laffs_no_scale


def draw(radius, ts, phis, color, size, ax):

    # upper bound on radii
    log_radius = math.log(radius)
    # if len(ts.shape) > 1:
    #print("foo draw: r = {}\n ts = {} \n phis = {} \n style = {}".format(log_radius, ts[:100], phis[:100], color))

    ts_logs = torch.log(ts)
    xs = torch.cos(phis) * ts_logs
    ys = torch.sin(phis) * ts_logs

    ax.plot(xs, ys, 'o', color=color, markersize=size)


def prepare_plot(radius: float, ax):

    log_radius = math.log(radius)

    ax.set_aspect(1.0)

    factor = 1.2
    ax.set_xlim((-factor*log_radius, factor*log_radius))
    ax.set_ylim((-factor*log_radius, factor*log_radius))

    circle = Circle((0, 0), log_radius, color='r', fill=False)
    ax.add_artist(circle)


# TODO export somehow?
def get_normals(normals, K):

    Hs = get_rectification_homographies(normals, K)
    Hs_as_affine = Hs[:, :, :2, :2]
    det_Hs = torch.det(Hs_as_affine).sqrt().unsqueeze(2).unsqueeze(3)
    Hs_as_affine = Hs_as_affine / det_Hs

    # TODO CONTINUE asserts=False did not work on the whole set of normals (index 3081)
    _, _, ts_h, phis_h = decompose_lin_maps(Hs_as_affine, asserts=True)

    return ts_h, phis_h


def get_aff_map(img, t, phi, component_mask):

    lin_map = compose_lin_maps(t, phi)
    aff_map = torch.zeros((1, 2, 3))
    aff_map[:, :2, :2] = lin_map

    coords = torch.where(component_mask)
    min_x = coords[1].min()
    max_x = coords[1].max()
    min_y = coords[0].min()
    max_y = coords[0].max()

    corner_pts = torch.tensor([[min_x, min_y],
                               [min_x, max_y],
                               [max_x, max_y],
                               [max_x, min_y]], dtype=torch.float)[None]

    H = KR.geometry.convert_affinematrix_to_homography(aff_map)
    corner_pts_new = KR.geometry.transform_points(H, corner_pts)

    aff_map[:, :, 2] = -torch.tensor([corner_pts_new[0, :, 0].min(), corner_pts_new[0, :, 1].min()])

    new_w = int((corner_pts_new[0, :, 0].max() - corner_pts_new[0, :, 0].min()).item())
    new_h = int((corner_pts_new[0, :, 1].max() - corner_pts_new[0, :, 1].min()).item())
    t_img = KR.image_to_tensor(img, False).float() / 255.

    return aff_map, new_h, new_w, t_img

@dataclass
class PointsStyle:
    ts: torch.tensor
    phis: torch.tensor
    color: str
    size: float


def plot_space_of_tilts(label, img_name, valid_component, normal_index, exp_r, point_styles: list, save_dir:str=None):

    fig, ax = plt.subplots()
    plt.title("{}: {} - {}th component -> {}th normal".format(label, img_name, valid_component, normal_index))
    prepare_plot(exp_r, ax)
    for point_style in point_styles:
        draw(exp_r, point_style.ts, point_style.phis, point_style.color, point_style.size, ax)
    # draw(exp_r, ts_affnet, phis_affnet, "b", 0.5, ax)
    # draw(exp_r, ts_mean_affnet, phis_mean_affnet, "r", 3, ax)
    # TODO save
    plt.show()

hard_net_filter = 5

def main():

    cache_laffs = False

    Timer.start()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    Timer.start_check_point("HardNetDescriptor")
    hardnet = HardNetDescriptor(sift_descriptor=cv.SIFT_create(None), device=device)
    Timer.end_check_point("HardNetDescriptor")

    pipeline = prepare_pipeline()

    exp_r = 5.8

    #dfl = ["frame_0000000070_2", "frame_0000001525_1", "frame_0000001865_1"]
    dfl = ["frame_0000000070_2"]
    for idx, img_name in enumerate(dfl):
        img_file_path = pipeline.scene_info.get_img_file_path(img_name)

        img = cv.cvtColor(cv.imread(img_file_path), cv.COLOR_BGR2RGB)
        # TODO provide img as a parameter to save some computation
        laffs_no_scale = get_laffs_no_scale_p_cached(img_file_path, img_name, hardnet, cache_laffs)
        _, _, ts, phis = decompose_lin_maps(laffs_no_scale[:, :, :, :2])

        plot_space_of_tilts("all", img_name, 0, 0, exp_r, [
            PointsStyle(ts=ts, phis=phis, color="b", size=0.5),
        ], save_dir=None)

        img_data = pipeline.process_image(img_name, idx % 2)[0]
        kpts_component_indices = get_kpts_components_indices(img_data.components_indices, img_data.valid_components_dict, laffs_no_scale)

        K = pipeline.scene_info.get_img_K(img_name)
        ts_h, phis_h = get_normals(img_data.normals, K)

         # -1 for invalid/no normal #AGAIN the ambiguity of whether 1 normal is (3,) it (1,3)
        assert len(img_data.normals.shape) == 2

        all_kps = []
        all_desc = np.zeros((0, 128))
        all_laffs_final = torch.zeros(1, 0, 2, 3)

        #for i in range(img_data.normals.shape[0]):
        for valid_component in img_data.valid_components_dict:

            normal_index = img_data.valid_components_dict[valid_component]

            print("processing component->normal: {} -> {}".format(valid_component, normal_index))

            ts_normal, phis_normal = ts_h[:, normal_index], phis_h[:, normal_index]
            mask = kpts_component_indices == valid_component
            ts_affnet, phis_affnet = ts[mask], phis[mask]
            t_mean_affnet = torch.mean(ts_affnet)
            phi_mean_affnet = torch.mean(phis_affnet)

            plot_space_of_tilts("unrectified", img_name, valid_component, normal_index, exp_r, [
                PointsStyle(ts=ts_normal, phis=phis_normal, color="black", size=5),
                PointsStyle(ts=ts_affnet, phis=phis_affnet, color="b", size=0.5),
                PointsStyle(ts=t_mean_affnet, phis=phi_mean_affnet, color="r", size=3)
            ], save_dir=None)


            # testing...
            #aff_maps, new_h, new_w, t_img = get_aff_map(img, torch.tensor([2.0]), torch.tensor([0.0]))
            mask_img_component = torch.from_numpy(img_data.components_indices == valid_component)
            aff_maps, new_h, new_w, t_img = get_aff_map(img, t_mean_affnet, phi_mean_affnet, mask_img_component)

            img_warped_t = KR.geometry.warp_affine(t_img, aff_maps, dsize=(new_h, new_w))
            img_warped = KR.tensor_to_image(img_warped_t * 255.0).astype(dtype=np.uint8)
            plt.figure(figsize=(6, 8))
            plt.title("{} - warped component {}, normal {}".format(img_name, valid_component, normal_index))
            plt.imshow(img_warped)
            plt.show()

            #cv.cvtColor(cv.imread(file_path), cv.COLOR_BGR2RGB)
            kps_warped, descs_warped, laffs_final = hardnet.detectAndCompute(img_warped, give_laffs=True, filter=hard_net_filter)

            aff_maps_inv = KR.geometry.transform.invert_affine_transform(aff_maps)

            kps_t = torch.tensor([kp.pt + (1,) for kp in kps_warped])
            kpt_s_back = aff_maps_inv.repeat(kps_t.shape[0], 1, 1) @ kps_t.unsqueeze(2)
            kpt_s_back = kpt_s_back.squeeze(2)

            # laffs_h = torch.zeros(laffs_final.shape[1:2] + (3,))
            # laffs_h[:, :2] = laffs_final[0, :, :, 2]
            # laffs_h[:, 2] = 1.0
            # new_laffs_coords = aff_maps_inv.repeat(laffs_h.shape[0], 1, 1) @ laffs_h.unsqueeze(2)
            # laffs_final[0, :, :, 2] = new_laffs_coords[:, :, 0]
            laffs_final[0, :, :, 2] = kpt_s_back

            kpt_s_back_int = torch.round(kpt_s_back).to(torch.long)
            ##(kpt_s_back[:, 0, 0].to(torch.long) <= img.shape[0]) & (kpt_s_back[:, 0, 0].to(torch.long) < 0)
            mask = (kpt_s_back_int[:, 1] < img.shape[0]) & (kpt_s_back_int[:, 1] >= 0) & (kpt_s_back_int[:, 0] < img.shape[1]) & (kpt_s_back_int[:, 0] >= 0)
            print("invalid back transformed pixels: {}/{}".format(mask.shape[0] - mask.sum(), mask.shape[0]))

            kpt_s_back_int[~mask, 0] = 0
            kpt_s_back_int[~mask, 1] = 0
            mask = (mask) & (img_data.components_indices[kpt_s_back_int[:, 1], kpt_s_back_int[:, 0]] == valid_component)
            mask = mask.to(torch.bool)

            kps = []
            for i, kp in enumerate(kps_warped):
                if mask[i]:
                    kp.pt = (kpt_s_back[i][0].item(), kpt_s_back[i][1].item())
                    kps.append(kp)

            descs = descs_warped[mask]
            laffs_final = laffs_final[:, mask]

            visualize_LAF(t_img, laffs_final, figsize=(8, 12))
            # scale_l_final = KF.get_laf_scale(laffs_final)
            # laffs_final = KF.scale_laf(laffs_final, 1. / scale_l_final)

            all_kps.extend(kps)
            all_desc = np.vstack((all_desc, descs))
            all_laffs_final = torch.cat((all_laffs_final, laffs_final), 1)

            _, _, ts_affnet_final, phis_affnet_final = decompose_lin_maps(laffs_final[:, :, :, :2])
            # ts_affnet_final_mean = torch.mean(ts_affnet_final)
            # phis_affnet_final_mean = torch.mean(phis_affnet_final)

            plot_space_of_tilts("rectified", img_name, valid_component, normal_index, exp_r, [
                PointsStyle(ts=ts_normal, phis=phis_normal, color="black", size=5),
                PointsStyle(ts=ts_affnet_final, phis=phis_affnet_final, color="b", size=0.5),
                #PointsStyle(ts=ts_affnet_final_mean, phis=phis_affnet_final_mean, color="r", size=3)
            ], save_dir=None)

        visualize_LAF(t_img, all_laffs_final, figsize=(8, 12))

            ## coords = torch.round(coords)
            # torch.clamp(coords[:, 0], 0, components_indices.shape[1] - 1, out=coords[:, 0])
            # torch.clamp(coords[:, 1], 0, components_indices.shape[0] - 1, out=coords[:, 1])
            # coords = coords.to(torch.long)


    Timer.log_stats()


def draw_test():

    radius = 1.7
    ts = torch.tensor([1.1319, 1.1319, 1.2136, 1.0592, 1.1286, 1.1663, 1.2231, 1.2231, 1.1127,
                 1.0755, 1.0778, 1.2580, 1.0351, 1.2732, 1.2732, 1.2206, 1.1509, 1.0904,
                 1.0982, 1.0432, 1.3149, 1.0875, 1.0457, 1.0357, 1.1554, 1.2691, 1.2691,
                 1.0834, 1.0124, 1.0124, 1.1793, 1.2422, 1.0970, 1.2479, 1.2478, 1.0951,
                 1.1189, 1.0922, 1.0922, 1.0861, 1.2390, 1.0681, 1.0788, 1.1042, 1.2036,
                 1.1800, 1.1800, 1.1368, 1.1368, 1.2107, 1.1130, 1.0565, 1.1601, 1.1735,
                 1.1735, 1.0962, 1.1921, 1.0994, 1.0994, 1.2375, 1.1585, 1.0956, 1.0956,
                 1.0915, 1.2143, 1.1329, 1.0327, 1.2184, 1.2300, 1.3192, 1.0538, 1.0538,
                 1.1630, 1.1138, 1.1916, 1.0691, 1.0681, 1.0289, 1.0809, 1.1317, 1.0821,
                 1.0266, 1.0524, 1.1183, 1.2366, 1.1436, 1.2093, 1.2093, 1.1545, 1.0598,
                 1.0598, 1.1063, 1.1583, 1.1583, 1.0944, 1.1603, 1.2142, 1.2142, 1.0572,
                 1.1491])
    phis = torch.tensor([0.1547, 0.1547, 1.4570, 1.7965, 0.2988, 0.0480, 1.3214, 1.3214, 0.7572,
                   0.2797, 1.0581, 1.5154, 2.9855, 0.1490, 0.1490, 0.5565, 0.1747, 0.4184,
                   0.8013, 2.0616, 0.2092, 1.0492, 0.5837, 2.8053, 0.6010, 3.1406, 3.1407,
                   1.0849, 1.4009, 1.4009, 1.5825, 0.3531, 0.3736, 0.2089, 0.2089, 1.0472,
                   0.8741, 1.0096, 1.0096, 3.0395, 0.3065, 1.6334, 1.3562, 1.2099, 0.3060,
                   0.6847, 0.6847, 0.8206, 0.8206, 0.1486, 1.1090, 3.0709, 1.2815, 0.2579,
                   0.2579, 0.8093, 0.1903, 0.5502, 0.5502, 0.4062, 1.2590, 1.3575, 1.3575,
                   1.3447, 0.5134, 0.2113, 0.3479, 1.1235, 3.0801, 0.2207, 0.8382, 0.8382,
                   0.2779, 0.2045, 1.3080, 2.6112, 0.3788, 0.7550, 1.2412, 0.6423, 3.0673,
                   2.3306, 1.7669, 1.3823, 0.8896, 0.2179, 0.4288, 0.4288, 0.9286, 0.5084,
                   0.5084, 0.7006, 0.4556, 0.4556, 1.4091, 0.2774, 1.0528, 1.0528, 1.0057,
                   0.5571])

    # plt.figure()
    fig, ax = plt.subplots()
    plt.title("img foo - 1st normal")
    prepare_plot(radius, ax)
    draw(radius, ts, phis, "b", 1, ax)
    plt.show()


if __name__ == "__main__":
    main()
    #draw_test()

# CONTINUE:
#   IMPORTANT: a) get_kpts_normals(normals, laffs_no_scale).unsqueeze(dim=0) -> get_kpts_normals_representatives!!!!
#   IMPORTANT: b) compare sets of affines (original or inverses) within one plane
#   IMPORTANT: c) compare sets of affines' decomposition (original or inverses) within one plane
#             -  basically the idea in Rodriquez is to ignore the tail of the distribution (cluster)

# (background - get_normal_vec_from_decomposition(ts, phis) is most likely wrong - at least because of the missing calibration
# get_normal_vec_from_decomposition - probably doesn't work as expected - implement 5,6 from affine_decomposition.pdf
