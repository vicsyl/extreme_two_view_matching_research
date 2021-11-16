import math
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from hard_net_descriptor import HardNetDescriptor
from normals_rotations import *

import kornia.geometry as KG
import cv2 as cv
import pickle
import traceback
import sys

import numpy as np
import torch
import argparse

from config import *
from connected_components import get_connected_components, get_and_show_components
from depth_to_normals import *
from matching import match_epipolar, match_find_F_degensac, match_images_with_dominant_planes
from rectification import possibly_upsample_normals, get_rectified_keypoints
from scene_info import SceneInfo
from utils import Timer
from img_utils import show_or_close, get_degrees_between_normals
from evaluation import *
from sky_filter import get_nonsky_mask
from clustering import Clustering, bilateral_filter

import matplotlib.pyplot as plt

two_hundred_permutation = [164, 90, 8, 35, 50, 112, 30, 51, 120, 78, 130, 134, 171, 5, 101, 147, 192, 72, 47, 156, 105,
                           22, 181, 129, 16, 198, 82, 100, 188, 159, 107, 86, 93, 151, 136, 96, 97, 83, 143, 0, 165,
                           185, 91, 7, 61, 12, 160, 92, 41, 184, 148, 76, 162, 157, 109, 20, 183, 17, 161, 132, 117,
                           178, 32, 111, 80, 153, 4, 180, 42, 116, 68, 95, 1, 189, 46, 170, 121, 139, 63, 58, 89, 177,
                           125, 75, 23, 167, 146, 2, 64, 94, 166, 145, 141, 6, 194, 197, 62, 172, 124, 193, 48, 24, 196,
                           85, 81, 60, 57, 88, 182, 126, 37, 169, 128, 39, 175, 11, 55, 40, 19, 65, 118, 84, 67, 69, 25,
                           43, 34, 168, 140, 137, 187, 150, 49, 186, 149, 59, 122, 144, 190, 9, 98, 174, 138, 102, 79,
                           66, 10, 110, 28, 29, 114, 77, 52, 123, 113, 108, 87, 33, 53, 199, 45, 179, 99, 135, 15, 73,
                           104, 131, 71, 31, 133, 176, 119, 191, 38, 155, 44, 3, 26, 18, 36, 154, 13, 173, 21, 27, 70,
                           152, 127, 54, 14, 163, 115, 103, 142, 56, 195, 158, 106, 74]


def parse_list(list_str: str):
    fields = list_str.split(",")
    fields = filter(lambda x: x != "", map(lambda x: x.strip(), fields))
    fields = list(fields)
    return fields


def ensure_key(map, key):
    if not map.keys().__contains__(key):
        map[key] = {}


def ensure_keys(map, keys_list):
    for i in range(len(keys_list)):
        ensure_key(map, keys_list[i])
        map = map[keys_list[i]]
    return map


def possibly_expand_normals(normals):
    if len(normals.shape) == 1:
        normals = np.expand_dims(normals, axis=0)
    return normals


@dataclass
class Pipeline:

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    stages = ["before_rectification", "before_matching", "final"]
    stages_map = {v: i for i, v in enumerate(stages)}

    def get_stage_number(self):
        return self.stages_map[self.config["pipeline_final_step"]]

    mean_shift = "mean"
    handle_antipodal_points = True
    singular_value_quantil = 1.0
    all_unrectified = False

    scene_name = None
    scene_type = None
    permutation_limit = None
    method = None
    file_name_suffix = None
    output_dir = None
    output_dir_prefix = None

    ransac_th = 0.5
    ransac_conf = 0.9999
    ransac_iters = 100000

    config = None
    cache_map = None
    stats_map = {}
    stats = {}

    # actually unused
    show_save_normals = False
    show_orig_image = True

    # ! FIXME not really compatible with matching pairs
    chosen_depth_files = None
    sequential_files_limit = None

    show_input_img = False

    show_clusters = True
    save_clusters = True
    show_clustered_components = True
    save_clustered_components = True
    show_rectification = True
    save_rectification = True
    show_sky_mask = True
    save_sky_mask = True
    show_matching = True
    save_matching = True

    #matching
    feature_descriptor = None
    #matching_dir = None
    matching_difficulties = None
    matching_limit = None
    matching_pairs = None

    planes_based_matching = False
    use_degensac = False
    estimate_k = False
    focal_point_mean_factor = 0.5

    rectify = True
    clip_angle = None

    knn_ratio_threshold = 0.85

    use_cached_img_data = True

    upsample_early = True

    # connected components
    connected_components_connectivity = 4
    connected_components_closing_size = None
    connected_components_flood_fill = False

    def setup_descriptor(self):
        feature_descriptor = self.config["feature_descriptor"]
        n_features = self.config["n_features"]
        use_hardnet = self.config["use_hardnet"]

        if feature_descriptor == "SIFT":
            low_level_descriptor = cv.SIFT_create(n_features)
        elif feature_descriptor == "BRISK":
            low_level_descriptor = cv.BRISK_create(n_features)

        if use_hardnet:
            self.feature_descriptor = HardNetDescriptor(low_level_descriptor, device=self.device)
        else:
            self.feature_descriptor = low_level_descriptor

    @staticmethod
    def configure(config_file_name: str, args):

        # https://docs.python.org/2/library/configparser.html
        #     import configparser
        #     config = configparser.ConfigParser()
        #     config.read("config.txt")
        #     cf = config['settings']
        #     sc = cf['save_clusters']
        #     print()

        pipeline = Pipeline()
        config = CartesianConfig.get_default_cfg()

        with open(config_file_name) as f:
            for line in f:

                if line.strip().startswith("#"):
                    continue
                elif line.strip() == "":
                    continue

                k, v = line.partition("=")[::2]
                k = k.strip()
                v = v.strip()

                if k == "scene_name":
                    pipeline.scene_name = v
                elif k == "handle_antipodal_points":
                    pipeline.handle_antipodal_points = v.lower() == "true"
                elif k == "device":
                    if v == "cpu":
                        pipeline.device = torch.device("cpu")
                    elif v == "cuda":
                        pipeline.device = torch.device("cuda")
                    else:
                        raise Exception("Unknown param value for 'device': {}".format(v))
                elif k == "mean_shift":
                    if v.lower() == "none":
                        pipeline.mean_shift = None
                    elif v.lower() == "mean":
                        pipeline.mean_shift = "mean"
                    elif v.lower() == "full":
                        pipeline.mean_shift = "full"
                    else:
                        raise Exception("Unknown param value for 'mean_shift': {}".format(v))
                elif k == "singular_value_quantil":
                    pipeline.singular_value_quantil = float(v)
                elif k == "permutation_limit":
                    pipeline.permutation_limit = int(v)
                elif k == "method":
                    pipeline.method = v
                elif k == "scene_type":
                    pipeline.scene_type = v
                elif k == "file_name_suffix":
                    pipeline.file_name_suffix = v
                elif k == "all_unrectified":
                    pipeline.all_unrectified = v.lower() == "true"
                elif k == "rectify":
                    pipeline.rectify = v.lower() == "true"
                elif k == "use_degensac":
                    pipeline.use_degensac = v.lower() == "true"
                elif k == "estimate_k":
                    pipeline.estimate_k = v.lower() == "true"
                elif k == "focal_point_mean_factor":
                    pipeline.focal_point_mean_factor = float(v)
                elif k == "knn_ratio_threshold":
                    pipeline.knn_ratio_threshold = float(v)
                elif k == "matching_difficulties_min":
                    matching_difficulties_min = int(v)
                elif k == "matching_difficulties_max":
                    matching_difficulties_max = int(v)
                elif k == "matching_limit":
                    pipeline.matching_limit = int(v)
                elif k == "planes_based_matching":
                    pipeline.planes_based_matching = v.lower() == "true"
                elif k == "output_dir":
                    pipeline.output_dir = v
                elif k == "show_input_img":
                    pipeline.show_input_img = v.lower() == "true"
                elif k == "show_matching":
                    pipeline.show_matching = v.lower() == "true"
                elif k == "save_matching":
                    pipeline.save_matching = v.lower() == "true"
                elif k == "show_clusters":
                    pipeline.show_clusters = v.lower() == "true"
                elif k == "save_clusters":
                    pipeline.save_clusters = v.lower() == "true"
                elif k == "show_clustered_components":
                    pipeline.show_clustered_components = v.lower() == "true"
                elif k == "save_clustered_components":
                    pipeline.save_clustered_components = v.lower() == "true"
                elif k == "show_rectification":
                    pipeline.show_rectification = v.lower() == "true"
                elif k == "save_rectification":
                    pipeline.save_rectification = v.lower() == "true"
                elif k == "show_sky_mask":
                    pipeline.show_sky_mask = v.lower() == "true"
                elif k == "save_sky_mask":
                    pipeline.save_sky_mask = v.lower() == "true"
                elif k == "do_flann":
                    Config.config_map[Config.key_do_flann] = v.lower() == "true"
                elif k == "matching_pairs":
                    pipeline.matching_pairs = parse_list(v)
                elif k == "chosen_depth_files":
                    pipeline.chosen_depth_files = parse_list(v)
                elif k == "use_cached_img_data":
                    pipeline.use_cached_img_data = v.lower() == "true"
                elif k == "output_dir_prefix":
                    pipeline.output_dir_prefix = v
                elif k == "ransac_th":
                    pipeline.ransac_th = float(v)
                elif k == "ransac_conf":
                    pipeline.ransac_conf = float(v)
                elif k == "ransac_iters":
                    pipeline.ransac_iters = int(v)
                elif k == "upsample_early":
                    pipeline.upsample_early = v.lower() == "true"
                elif k == "clip_angle":
                    if v.lower() == "none":
                        pipeline.clip_angle = None
                    else:
                        pipeline.clip_angle = int(v)
                elif k == "connected_components_connectivity":
                    value = int(v)
                    assert value == 4 or value == 8, "connected_components_connectivity must be 4 or 8"
                    pipeline.connected_components_connectivity = value
                elif k == "connected_components_closing_size":
                    if v.lower() == "none":
                        pipeline.connected_components_closing_size = None
                    else:
                        pipeline.connected_components_closing_size = int(v)
                elif k == "connected_components_flood_fill":
                    pipeline.connected_components_flood_fill = v.lower() == "true"
                else:
                    CartesianConfig.config_parse_line(k, v, config)

        pipeline.matching_difficulties = list(range(matching_difficulties_min, matching_difficulties_max))

        if args is not None and args.__contains__("output_dir"):
            pipeline.output_dir = args.output_dir
        elif pipeline.output_dir is None:
            pipeline.output_dir = append_all(pipeline, pipeline.output_dir_prefix)

        assert not pipeline.planes_based_matching or pipeline.rectify, "rectification must be on for planes_based_matching"

        return pipeline, config

    def start(self):
        print("is torch.cuda.is_available(): {}".format(torch.cuda.is_available()))
        print("device: {}".format(self.device))

        self.log()
        self.scene_info = SceneInfo.read_scene(self.scene_name, self.scene_type, file_name_suffix=self.file_name_suffix)
        self.setup_descriptor()

        scene_length = len(self.scene_info.img_pairs_lists)
        scene_length_range = range(0, scene_length)
        if self.matching_pairs is not None:
            self.matching_difficulties = scene_length_range

        self.depth_input_dir = self.scene_info.depth_input_dir()
        intersection = set(self.matching_difficulties).intersection(set(scene_length_range))
        self.matching_difficulties = list(intersection)


    def log(self):
        print("Pipeline config:")
        attr_list = [attr for attr in dir(self) if not callable(getattr(self, attr)) and not attr.startswith("__") and attr not in ["stats", "stats_map"]]
        for attr_name in attr_list:
            if attr_name in ["scene_info"]:
                continue
            print("\t{}\t{}".format(attr_name, getattr(self, attr_name)))
        print()

        Config.log()
        Clustering.log()

    def get_img_processing_dir(self):
        return "{}/imgs/{}".format(self.output_dir, self.cache_map[Property.cache_img_data])

    def get_and_create_img_processing_dir(self):
        img_processing_dir = self.get_img_processing_dir()
        if not os.path.exists(img_processing_dir):
            Path(img_processing_dir).mkdir(parents=True, exist_ok=True)
        return img_processing_dir

    def get_cached_image_data_or_none(self, img_name, img, real_K):
        img_processing_dir = self.get_and_create_img_processing_dir()
        img_data_path = "{}/{}_img_data.pkl".format(img_processing_dir, img_name)
        if self.use_cached_img_data and os.path.isfile(img_data_path):
            Timer.start_check_point("reading img processing data")
            with open(img_data_path, "rb") as f:
                print("img data for {} already computed, reading: {}".format(img_name, img_data_path))
                img_serialized_data: ImageSerializedData = pickle.load(f)
            Timer.end_check_point("reading img processing data")
            return ImageData.from_serialized_data(img=img,
                                                  real_K=real_K,
                                                  img_serialized_data=img_serialized_data)
        else:
            return None

    @staticmethod
    def save_img_data(img_data, img_data_path, img_name):
        Timer.start_check_point("saving img data")
        with open(img_data_path, "wb") as f:
            print("img data for {} saving into: {}".format(img_name, img_data_path))
            pickle.dump(img_data.to_serialized_data(), f)
        Timer.end_check_point("saving img data")

    def process_image(self, img_name, order):

        Timer.start_check_point("processing img")
        print("Processing: {}".format(img_name))
        img_processing_dir = self.get_and_create_img_processing_dir()
        Path(img_processing_dir).mkdir(parents=True, exist_ok=True)

        img_file_path = self.scene_info.get_img_file_path(img_name)
        img = cv.imread(img_file_path, None)
        if self.show_input_img:
            plt.figure(figsize=(9, 9))
            plt.title(img_name)
            plt.imshow(img)
            show_or_close(True)

        orig_height = img.shape[0]
        orig_width = img.shape[1]
        real_K = self.scene_info.get_img_K(img_name)
        if self.estimate_k:
            focal_length = (orig_width + orig_height) * self.focal_point_mean_factor
            K_for_rectification = np.array([
                [focal_length, 0,            orig_width / 2.0],
                [0,            focal_length, orig_height / 2.0],
                [0,            0,            1]
            ])
        else:
            K_for_rectification = real_K
            focal_length = real_K[0, 0]
            assert abs(real_K[0, 2] * 2 - orig_width) < 0.5
            assert abs(real_K[1, 2] * 2 - orig_height) < 0.5

        img_data_path = "{}/{}_img_data.pkl".format(img_processing_dir, img_name)

        if not self.rectify:

            Timer.end_check_point("processing img without rectification")

            cached_img_data = self.get_cached_image_data_or_none(img_name, img, real_K)
            if cached_img_data is not None:
                return cached_img_data

            kps, descs = self.feature_descriptor.detectAndCompute(img, None)

            img_data = ImageData(img=img,
                             real_K=real_K,
                             key_points=kps,
                             descriptions=descs,
                             normals=None,
                             components_indices=None,
                             valid_components_dict=None)

            Pipeline.save_img_data(img_data, img_data_path, img_name)

            Timer.end_check_point("processing img without rectification")
            return img_data

        else:

            cached_img_data = self.get_cached_image_data_or_none(img_name, img, real_K)
            if cached_img_data is not None:
                return cached_img_data

            Timer.start_check_point("processing img from scratch")

            depth_data_file_name = "{}.npy".format(img_name)
            depth_data = read_depth_data(depth_data_file_name, self.depth_input_dir, device=torch.device('cpu'))
            normals, s_values = compute_normals_from_svd(focal_length, orig_height, orig_width, depth_data, device=torch.device('cpu'))

            Timer.start_check_point("sky_mask")
            non_sky_mask = get_nonsky_mask(img, normals.shape[0], normals.shape[1])
            Timer.end_check_point("sky_mask")

            Timer.start_check_point("quantil_mask")
            quantil_mask = self.get_quantil_mask(img, img_name, self.singular_value_quantil, depth_data[2:4], s_values, depth_data, non_sky_mask)
            filter_mask = non_sky_mask & quantil_mask
            Timer.end_check_point("quantil_mask")

            Timer.start_check_point("clustering")
            normals_deviced = normals.to(self.device)
            print("normals_deviced.device: {}".format(normals_deviced.device))
            normals_clusters_repr, normal_indices, valid_normals = cluster_normals(normals_deviced,
                                                                                   filter_mask=filter_mask,
                                                                                   mean_shift=self.mean_shift,
                                                                                   device=self.device,
                                                                                   handle_antipodal_points=self.handle_antipodal_points)
            Timer.end_check_point("clustering")
            self.update_normals_stats(normal_indices, normals_clusters_repr, valid_normals, self.cache_map[Property.all_combinations], img_name)

            show_or_save_clusters(normals,
                                  normal_indices,
                                  normals_clusters_repr,
                                  img_processing_dir,
                                  depth_data_file_name,
                                  show=self.show_clusters,
                                  save=self.save_clusters)

            if self.upsample_early:
                normal_indices = possibly_upsample_normals(img, normal_indices)

            valid_normal_indices = []
            for i, normal in enumerate(normals_clusters_repr):
                angle_rad = math.acos(np.dot(normal, np.array([0, 0, -1])))
                angle_degrees = angle_rad * 180 / math.pi
                # print("angle: {} vs. angle threshold: {}".format(angle_degrees, Config.plane_threshold_degrees))
                if angle_degrees >= Config.plane_threshold_degrees:
                    # print("WARNING: two sharp of an angle with the -z axis, skipping the rectification")
                    continue
                else:
                    # print("angle ok")
                    valid_normal_indices.append(i)

            components_indices, valid_components_dict = get_connected_components(normal_indices, valid_normal_indices,
                                                                                 closing_size=self.connected_components_closing_size,
                                                                                 flood_filling=self.connected_components_flood_fill,
                                                                                 connectivity=self.connected_components_connectivity)

            if not self.upsample_early:
                assert np.all(components_indices < 256), "could not retype to np.uint8"
                components_indices = components_indices.astype(dtype=np.uint8)
                components_indices = possibly_upsample_normals(img, components_indices)
                components_indices = components_indices.astype(dtype=np.uint32)

            components_out_path = "{}/{}_cluster_connected_components".format(img_processing_dir, img_name)
            get_and_show_components(components_indices,
                                    valid_components_dict,
                                    normals=normals_clusters_repr,
                                    show=self.show_clustered_components,
                                    save=self.save_clustered_components,
                                    path=components_out_path,
                                    file_name=depth_data_file_name[:-4])

            if self.config["recify_by_fixed_rotation"] or self.get_stage_number() <= self.stages_map["before_rectification"]:

                kps, descs = None, None

            else:

                if order == 0:
                    rotation_factor = self.config["rotation_alpha1"]
                else:
                    rotation_factor = self.config["rotation_alpha2"]

                # get rectification
                rectification_path_prefix = "{}/{}".format(img_processing_dir, img_name)
                kps, descs, unrectified_indices = get_rectified_keypoints(normals_clusters_repr,
                                                     components_indices,
                                                     valid_components_dict,
                                                     img,
                                                     K_for_rectification,
                                                     descriptor=self.feature_descriptor,
                                                     img_name=img_name,
                                                     clip_angle=self.clip_angle,
                                                     show=self.show_rectification,
                                                     save=self.save_rectification,
                                                     out_prefix=rectification_path_prefix,
                                                     rotation_factor=rotation_factor,
                                                     all_unrectified=self.all_unrectified
                                                     )

            img_data = ImageData(img=img,
                                 real_K=real_K,
                                 key_points=kps,
                                 descriptions=descs,
                                 normals=normals_clusters_repr,
                                 components_indices=components_indices,
                                 valid_components_dict=valid_components_dict)

            Timer.end_check_point("processing img from scratch")

            Pipeline.save_img_data(img_data, img_data_path, img_name)

            return img_data

    def get_nonsky_mask_possibly_cached(self, img_name, np_image, h_w):
        height, width = h_w
        Timer.start_check_point("sky_mask")
        sky_cache_dir = "{}/cache/sky".format(self.output_dir)
        if not os.path.exists(sky_cache_dir):
            Path(sky_cache_dir).mkdir(parents=True, exist_ok=True)
        sky_cache_file = "{}/{}.npy".format(sky_cache_dir, img_name)
        if os.path.isfile(sky_cache_file):
            with open(sky_cache_file, "rb") as f:
                ret = pickle.load(f)
        else:
            ret = get_nonsky_mask(np_image, height, width, self.device == torch.device("cuda"))
            with open(sky_cache_file, "wb") as f:
                pickle.dump(ret, f)
        Timer.end_check_point("sky_mask")
        return ret

    def compute_normals_possibly_cached(self,
            img_name,
            focal_length,
            orig_height,
            orig_width,
            depth_data,
            simple_weighing=True,
            smaller_window=False,
            device=torch.device('cpu'),
            use_cache=True
    ):

        Timer.start_check_point("compute_normals")
        cache_file = None
        ret = None
        if use_cache:
            cache_dir = "{}/cache/normals".format(self.output_dir)
            if not os.path.exists(cache_dir):
                Path(cache_dir).mkdir(parents=True, exist_ok=True)
            cache_file = "{}/{}_{}.npy".format(cache_dir, img_name, Config.svd_weighted_sigma)
            if os.path.isfile(cache_file):
                with open(cache_file, "rb") as f:
                    ret = pickle.load(f)
        if ret is None:
            ret = compute_normals_from_svd(focal_length, orig_height, orig_width, depth_data,
                                           simple_weighing, smaller_window, device)
            if use_cache:
                with open(cache_file, "wb") as f:
                    pickle.dump(ret, f)
        Timer.end_check_point("compute_normals")
        return ret

    def update_normals_stats(self, normal_indices, normals_clusters_repr, valid_normals, params_key, img_name):

        sums = np.array([np.sum(normal_indices == i) for i in range(len(normals_clusters_repr))])
        indices = np.argsort(-sums)

        # eventually delete this check and the two lines above
        for i in range(len(indices)):
            if i != indices[i]:
                print("Warning: i != indices[i]")

        normals_clusters_repr_sorted = normals_clusters_repr[indices]

        degrees_list = get_degrees_between_normals(normals_clusters_repr_sorted)
        print("pipeline:compute_img_normals:normals_clusters_repr_sorted: {}".format(normals_clusters_repr_sorted))
        print("pipeline:compute_img_normals:degrees_list: {}".format(degrees_list))
        self.update_stats_map(["normals_degrees", params_key, img_name], degrees_list)
        self.update_stats_map(["normals", params_key, img_name], normals_clusters_repr)
        self.update_stats_map(["valid_normals", params_key, img_name], valid_normals)

    def get_quantil_mask(self, img, img_name, singular_value_quantil, h_w_size, s_values, depth_data, sky_mask_np):
        if singular_value_quantil == 1.0:
            mask = np.ones(h_w_size, dtype=bool)
        else:
            s_values_ratio = True
            if s_values_ratio:
                singular_values_order = s_values[:, :, 2] / torch.clamp(s_values[:, :, 1], min=1e-19)
            else:
                singular_values_order = s_values[:, :, 2]
                singular_values_order = singular_values_order / torch.clamp(depth_data[0, 0], min=1e-19)
            singular_values_order = singular_values_order + (1 - sky_mask_np) * singular_values_order.max().item()

            h, w = singular_values_order.shape[0], singular_values_order.shape[1]
            singular_values_order = singular_values_order.reshape(h * w)
            _, indices = torch.sort(singular_values_order)

            mask = torch.zeros_like(singular_values_order, dtype=torch.bool)
            non_sky = sky_mask_np.sum()
            mask[indices[:int(non_sky * singular_value_quantil)]] = True
            mask = mask.reshape(h, w).numpy()

            show_sky_mask(img, mask, img_name, show=self.show_sky_mask, save=False, title="quantile mask")
            show_sky_mask(img, sky_mask_np & mask, img_name, show=self.show_sky_mask, save=False, title="quantile and sky mask")

        return mask

    def compute_img_normals(self, img, img_name, stats_key_prefix=None, use_normals_cache=True):

        Timer.start_check_point("processing img", img_name)
        print("processing img {}".format(img_name))

        # get focal_length
        orig_height = img.shape[0]
        orig_width = img.shape[1]
        if self.estimate_k:
            focal_length = (orig_width + orig_height) * self.focal_point_mean_factor
        else:
            real_K = self.scene_info.get_img_K(img_name)
            focal_length = real_K[0, 0]
            assert abs(real_K[0, 2] * 2 - orig_width) < 0.5
            assert abs(real_K[1, 2] * 2 - orig_height) < 0.5

        depth_data_file_name = "{}.npy".format(img_name)
        depth_data = read_depth_data(depth_data_file_name, self.depth_input_dir, height=None, width=None, device=torch.device('cpu'))
        print("depth_data device {}".format(depth_data.device))

        img_processing_dir = self.get_and_create_img_processing_dir()
        sky_out_path = "{}/{}_sky_mask.jpg".format(img_processing_dir, img_name)
        # filter_mask is a numpy array
        sky_mask_np = self.get_nonsky_mask_possibly_cached(img_name, img, depth_data.shape[2:4])
        show_sky_mask(img, sky_mask_np, img_name, show=self.show_sky_mask, save=self.save_sky_mask, path=sky_out_path)

        counter = 0
        for sigma in [0.8, 1.2, 1.6]:

            Config.svd_weighted_sigma = sigma

            orig_normals, s_values = self.compute_normals_possibly_cached(img_name,
                                                                          focal_length,
                                                                          orig_height,
                                                                          orig_width,
                                                                          depth_data,
                                                                          simple_weighing=True,
                                                                          smaller_window=(sigma == 0.0),
                                                                          device=torch.device('cpu'),
                                                                          use_cache=use_normals_cache)

            print("orig_normals.device: {}".format(orig_normals.device))

            for normal_sigma in [None]:

                if normal_sigma is None:
                    normals = orig_normals
                else:
                    bf_key = "bilateral_filter_{}".format(normal_sigma)
                    Timer.start_check_point(bf_key)
                    normals = bilateral_filter(orig_normals, filter_mask=sky_mask_np, normal_sigma=normal_sigma, device=torch.device('cpu'))
                    print("normals.device after bilateral_filter: {}".format(normals.device))
                    Timer.end_check_point(bf_key)

                for mean_shift in ["full", "mean", None]:

                    for singular_value_quantil in [0.6, 0.8, 1.0]:

                        for angle_distance_threshold_degrees in [5, 10, 15, 20, 25, 30, 35]:

                            for adaptive in [False]:

                                if adaptive is True and mean_shift != "full":
                                    continue

                                compute = False
                                # if mean_shift is None and singular_value_quantil == 1.0 and angle_distance_threshold_degrees == 25 and sigma == 0.8:
                                #     compute = True
                                # if mean_shift in ["mean", "full"] and angle_distance_threshold_degrees == 35 and sigma == 0.8:
                                #     compute = True
                                if mean_shift in ["mean"] and angle_distance_threshold_degrees in [25] and sigma == 0.8 and singular_value_quantil in [0.8, 1.0]:
                                    compute = True
                                if not compute:
                                    continue

                                counter = counter + 1

                                ms_str = "ms_{}".format(mean_shift)
                                params_key = "{}_{}_{}_{}_{}_{}".format(ms_str, singular_value_quantil, angle_distance_threshold_degrees, sigma, normal_sigma, adaptive)
                                if stats_key_prefix is not None:
                                    params_key = "{}_{}".format(stats_key_prefix, params_key)

                                print("params_key: {}".format(params_key))
                                print("Params: s_value_hist_ratio: {}, "
                                      "angle_distance_threshold_degrees: {}, "
                                      "svd sigma: {}, "
                                      "mean shift step: {},"
                                      "normal sigma: {},"
                                      "adaptive: {}"
                                      .format(singular_value_quantil, angle_distance_threshold_degrees, sigma, ms_str, normal_sigma, adaptive))

                                Clustering.angle_distance_threshold_degrees = angle_distance_threshold_degrees
                                Clustering.recompute(math.sqrt(singular_value_quantil))

                                quantil_mask = self.get_quantil_mask(img, img_name, singular_value_quantil, depth_data[2:4], s_values, depth_data, sky_mask_np)
                                filter_mask = sky_mask_np & quantil_mask

                                cp_key = "clustering_{}_{}".format(mean_shift, adaptive)
                                Timer.start_check_point(cp_key)
                                normals_deviced = normals.to(self.device)
                                print("normals_deviced.device: {}".format(normals_deviced.device))
                                normals_clusters_repr, normal_indices, valid_normals = cluster_normals(normals_deviced,
                                                                                                       filter_mask=filter_mask,
                                                                                                       mean_shift=mean_shift,
                                                                                                       adaptive=adaptive,
                                                                                                       return_all=True,
                                                                                                       device=self.device,
                                                                                                       handle_antipodal_points=True)

                                Timer.end_check_point(cp_key)
                                self.update_normals_stats(normal_indices, normals_clusters_repr, valid_normals, params_key, img_name)

                                show_or_save_clusters(normals,
                                                      normal_indices,
                                                      normals_clusters_repr,
                                                      img_processing_dir,
                                                      "{}: {} ".format(depth_data_file_name, params_key),
                                                      show=self.show_clusters,
                                                      save=self.save_clusters)
        print("counter: {}".format(counter))

    def update_stats_map(self, key_list, obj):
        map = ensure_keys(self.stats, key_list[:-1])
        map[key_list[-1]] = obj

    def run_sequential_pipeline(self):

        self.start()

        file_names, _ = self.scene_info.get_megadepth_file_names_and_dir(self.sequential_files_limit, self.chosen_depth_files)
        for idx, depth_data_file_name in enumerate(file_names):
            self.process_image(depth_data_file_name[:-4], idx)

        self.save_stats("sequential")

    def show_and_read_img_from_path(self, img_name, img_file_path):
        img = cv.imread(img_file_path, None)
        if self.show_input_img:
            plt.figure(figsize=(9, 9))
            plt.title(img_name)
            plt.imshow(img)
            show_or_close(True)
        return img

    def show_and_read_img(self, img_name):
        img_file_path = self.scene_info.get_img_file_path(img_name)
        return self.show_and_read_img_from_path(img_name, img_file_path)

    def run(self):
        if self.method == "compute_normals":
            self.run_sequential_for_normals()
        elif self.method == "compute_normals_compare":
            self.run_sequential_for_normals_compare()
        elif self.method == "run_sequential_pipeline":
            self.run_sequential_pipeline()
        elif self.method == "run_matching_pipeline":
            self.run_matching_pipeline()
        else:
            print("incorrect value of '{}' for method. Choose one from 'compute_normals', 'run_sequential_pipeline' or 'run_matching_pipeline'".format(self.method))

    def run_sequential_for_normals_compare(self):

        self.start()

        globs_and_prefixes = [("depth_data/megadepth_ds/depths/*_orig.npy", "gt"),
                              ("depth_data/megadepth_ds/depths/*_o.npy", "estimated")]

        self.depth_input_dir = "depth_data/megadepth_ds/depths"
        imgs_base = "depth_data/megadepth_ds/imgs"

        for glob_str, prefix in globs_and_prefixes:

            gl = glob.glob(glob_str)
            if gl is None or len(gl) == 0:
                raise Exception("nothing under {}".format(glob_str))

            file_names = [path.split("/")[-1] for path in gl]
            print("file_names: {}".format(file_names))

            for focal_point_mean_factor in [0.47]: #5, 0.5, 0.55]:
                self.focal_point_mean_factor = focal_point_mean_factor

                for i, depth_data_file_name in enumerate(file_names):
                    # if not depth_data_file_name.__contains__("101426383_d1d108b4c5"):
                    #     continue
                    if not depth_data_file_name.__contains__("110724086_954bbd1a5e"):
                        continue

                    img_name = depth_data_file_name[:-4]
                    img_name_img = img_name[:-5] if img_name.endswith("orig") else img_name
                    img_path = "{}/{}.jpg".format(imgs_base, img_name_img)
                    img = self.show_and_read_img_from_path(img_name, img_path)

                    if prefix == "gt":
                        sky_cache_dir = "{}/cache/sky".format(self.output_dir)
                        if not os.path.exists(sky_cache_dir):
                            Path(sky_cache_dir).mkdir(parents=True, exist_ok=True)
                        sky_cache_files = ["{}/{}.npy".format(sky_cache_dir, img_name), "{}/{}.npy".format(sky_cache_dir, img_name[:-5])]
                        depth_data = read_depth_data(depth_data_file_name, self.depth_input_dir, height=None, width=None, device=torch.device('cpu'))
                        sky_mask = (depth_data[0, 0] != 0.0).to(torch.int).numpy()
                        for sky_cache_file in sky_cache_files:
                            with open(sky_cache_file, "wb") as f:
                                pickle.dump(sky_mask, f)

                    prefix_full = "{}_{}".format(prefix, focal_point_mean_factor)
                    self.compute_img_normals(img, img_name, prefix_full, use_normals_cache=False)
                    if i % 10 == 0:
                        self.save_stats("normals_{}".format(i))
                    evaluate_normals_stats(self.stats)
                    Timer.log_stats()

        self.save_stats("normals")

    def run_sequential_for_normals(self):

        self.start()

        file_names, _ = self.scene_info.get_megadepth_file_names_and_dir(self.sequential_files_limit, self.chosen_depth_files)
        file_names_permuted = [file_names[two_hundred_permutation[i]] for i in range(self.permutation_limit)]
        for i, depth_data_file_name in enumerate(file_names_permuted):
            img_name = depth_data_file_name[:-4]
            img = self.show_and_read_img(img_name)
            self.compute_img_normals(img, img_name)
            if i % 10 == 0:
                self.save_stats("normals_{}".format(i))
            evaluate_normals_stats(self.stats)
            Timer.log_stats()

        self.save_stats("normals")

    def update_matching_stats(self, key, difficulty, img_pair_name, stats_struct: Stats):
        self.update_stats_map(["matching", key, difficulty, img_pair_name, "kps1"], stats_struct.all_features_1)
        self.update_stats_map(["matching", key, difficulty, img_pair_name, "kps2"], stats_struct.all_features_2)
        self.update_stats_map(["matching", key, difficulty, img_pair_name, "tentatives"], stats_struct.tentative_matches)
        self.update_stats_map(["matching", key, difficulty, img_pair_name, "inliers"], stats_struct.inliers)

    def get_diff_stats_file(self, difficulty=None):
        diff_str = "all" if difficulty is None else str(difficulty)
        diff_stats_dir = "{}/stats/{}".format(self.output_dir, self.cache_map[Property.all_combinations])
        if not os.path.exists(diff_stats_dir):
            Path(diff_stats_dir).mkdir(parents=True, exist_ok=True)
        return "{}/stats_diff_{}.pkl".format(diff_stats_dir, diff_str)

    def get_stats_key(self):
        return self.cache_map[Property.all_combinations]

    def ensure_stats_key(self, map, suffix=""):
        key = self.get_stats_key() + suffix
        if not map.__contains__(key):
            map[key] = {}

    def do_matching(self, image_data, img_pair, matching_out_dir, stats_map_diff, difficulty):

        if self.planes_based_matching:
            E, inlier_mask, src_pts, dst_pts, tentative_matches = match_images_with_dominant_planes(
                image_data[0],
                image_data[1],
                use_degensac=self.use_degensac,
                find_fundamental=self.estimate_k,
                img_pair=img_pair,
                out_dir=matching_out_dir,
                show=self.show_matching,
                save=self.save_matching,
                ratio_thresh=self.knn_ratio_threshold,
                ransac_th=self.ransac_th,
                ransac_conf=self.ransac_conf,
                ransac_iters=self.ransac_iters
            )

        elif self.use_degensac:
            E, inlier_mask, src_pts, dst_pts, tentative_matches = match_find_F_degensac(
                image_data[0].img,
                image_data[0].key_points,
                image_data[0].descriptions,
                image_data[0].real_K,
                image_data[1].img,
                image_data[1].key_points,
                image_data[1].descriptions,
                image_data[1].real_K,
                img_pair,
                matching_out_dir,
                show=self.show_matching,
                save=self.save_matching,
                ratio_thresh=self.knn_ratio_threshold,
                ransac_th=self.ransac_th,
                ransac_conf=self.ransac_conf,
                ransac_iters=self.ransac_iters
            )

        else:
            # NOTE using img_datax.real_K for a call to findE
            E, inlier_mask, src_pts, dst_pts, tentative_matches = match_epipolar(
                image_data[0].img,
                image_data[0].key_points,
                image_data[0].descriptions,
                image_data[0].real_K,
                image_data[1].img,
                image_data[1].key_points,
                image_data[1].descriptions,
                image_data[1].real_K,
                find_fundamental=self.estimate_k,
                img_pair=img_pair,
                out_dir=matching_out_dir,
                show=self.show_matching,
                save=self.save_matching,
                ratio_thresh=self.knn_ratio_threshold,
                ransac_th=self.ransac_th,
                ransac_conf=self.ransac_conf,
                ransac_iters=self.ransac_iters,
                cfg=self.config,
            )

            if E is None:
                # TODO test this
                ValueError("E is None")

        stats_struct = evaluate_matching(self.scene_info,
                                         E,
                                         image_data[0].key_points,
                                         image_data[1].key_points,
                                         tentative_matches,
                                         inlier_mask,
                                         img_pair,
                                         stats_map_diff,
                                         image_data[0].normals,
                                         image_data[1].normals,
                                         )

        self.update_matching_stats(self.cache_map[Property.all_combinations], difficulty,
                                   "{}_{}".format(img_pair.img1, img_pair.img2), stats_struct)

    def estimate_rotation_via_normals(self, normals1, normals2, img_pair, pair_key, zero_around_z):

        # get R
        normals1 = possibly_expand_normals(normals1)
        normals2 = possibly_expand_normals(normals2)
        solutions = find_sorted_rotations(normals1, normals2, zero_around_z)

        r_vec_first = solutions[0].rotation_vector
        r_matrix_first = get_rotation_matrix_safe(r_vec_first)
        first_err = compare_R_to_GT(img_pair, self.scene_info, r_matrix_first)
        GT_err = compare_R_to_GT(img_pair, self.scene_info, np.eye(3))
        GT_mat, _ = get_GT_R_t(img_pair, self.scene_info)
        GT_vec = KG.rotation_matrix_to_angle_axis(torch.from_numpy(GT_mat)[None]).detach().cpu().numpy()[0]
        print("normals counts: ({}, {})".format(normals1.shape[0], normals2.shape[0]))
        print("estimate_rotation_via_normals: I error against GT: {}".format(GT_err))
        print("estimate_rotation_via_normals: error against GT: {}".format(first_err))
        print("estimate_rotation_via_normals: objective function value: {}".format(solutions[0].objective_fnc))
        print("estimate_rotation_via_normals: rotation vector GT: {}".format(GT_vec))
        print("estimate_rotation_via_normals: rotation vector: {}".format(solutions[0].rotation_vector))

        top_5_err = first_err
        for solution in solutions[1:5]:
            r = get_rotation_matrix_safe(solution.rotation_vector)
            err_q = compare_R_to_GT(img_pair, self.scene_info, r)
            if err_q < top_5_err:
                top_5_err = err_q

        print("estimate_rotation_via_normals: top_5_err: {}".format(top_5_err))

        return r_vec_first

    def rectify_by_fixed_rotation_update(self, img_data, r, img_name):

        if self.estimate_k:
            raise NotImplemented("img_data.real_K != K_for_rectification")

        # get rectification
        rectification_path_prefix = "{}/{}".format(self.get_img_processing_dir(), img_name)
        kps, descs, _ = get_rectified_keypoints(img_data.normals,
                                                img_data.components_indices,
                                                img_data.valid_components_dict,
                                                img_data.img,
                                                img_data.real_K,  # K_for_rectification,
                                                descriptor=self.feature_descriptor,
                                                img_name=img_name,
                                                fixed_rotation_vector=r,
                                                clip_angle=self.clip_angle,
                                                show=self.show_rectification,
                                                save=self.save_rectification,
                                                out_prefix=rectification_path_prefix,
                                                all_unrectified=self.all_unrectified
                                                )

        img_data.key_points = kps
        img_data.descriptions = descs
        return img_data

    def run_matching_pipeline(self):

        self.start()

        self.ensure_stats_key(self.stats_map)
        already_processed = set()

        stats_counter = 0

        for difficulty in self.matching_difficulties:
            print("Difficulty: {}".format(difficulty))

            stats_map_diff = {}
            self.stats_map[self.get_stats_key()][difficulty] = stats_map_diff

            processed_pairs = 0
            for img_pair in self.scene_info.img_pairs_lists[difficulty]:

                pair_key = SceneInfo.get_key(img_pair.img1, img_pair.img2)
                if self.matching_pairs is not None:
                    if pair_key not in self.matching_pairs or pair_key in already_processed:
                        continue
                    else:
                        already_processed.add(pair_key)

                if self.matching_pairs is None and self.matching_limit is not None and processed_pairs >= self.matching_limit:
                    print("Reached matching limit of {} for difficulty {}".format(self.matching_limit, difficulty))
                    break

                Timer.start_check_point("complete image pair matching")
                print("Will be matching pair {}".format(pair_key))
                stats_counter = stats_counter + 1

                try:

                    matching_out_dir = "{}/matching".format(self.output_dir)
                    Path(matching_out_dir).mkdir(parents=True, exist_ok=True)

                    # I might not need normals yet
                    # img1, K_1, kps1, descs1, normals1, components_indices1, valid_components_dict1

                    image_data = []
                    try:
                        for idx, img in enumerate([img_pair.img1, img_pair.img2]):
                            image_data.append(self.process_image(img, idx))
                    except:
                        print("(processing image) {}_{} couldn't be processed, skipping the matching pair".format(img_pair.img1, img_pair.img1))
                        print(traceback.format_exc(), file=sys.stdout)
                        continue

                    if self.rectify:
                        zero_around_z = self.config["recify_by_0_around_z"]
                        estimated_r_vec = self.estimate_rotation_via_normals(image_data[0].normals, image_data[1].normals, img_pair, pair_key, zero_around_z)

                    if self.config["recify_by_fixed_rotation"]:

                        if self.config["recify_by_GT"]:
                            GT_R, _ = get_GT_R_t(img_pair, self.scene_info)
                            r_vec_full = KG.rotation_matrix_to_angle_axis(torch.from_numpy(GT_R)[None]).detach().cpu().numpy()[0]
                        else:
                            r_vec_full = estimated_r_vec

                        for idx, image_data_item in enumerate(image_data):
                            if idx == 0:
                                r_vec = r_vec_full / 2
                                img_name = img_pair.img1
                            else:
                                r_vec = -r_vec_full / 2
                                img_name = img_pair.img2

                            self.rectify_by_fixed_rotation_update(image_data_item, r_vec, img_name)

                    if self.get_stage_number() >= self.stages_map["final"]:
                        self.do_matching(image_data, img_pair, matching_out_dir, stats_map_diff, difficulty)

                    processed_pairs = processed_pairs + 1

                except:
                    print("(matching) {} couldn't be processed, skipping the matching pair".format(pair_key))
                    print(traceback.format_exc(), file=sys.stdout)

                Timer.end_check_point("complete image pair matching")

                if stats_counter % 10 == 0:
                    evaluate_stats(self.stats)
                evaluate_all_matching_stats(self.stats_map, n_examples=30, special_diff=difficulty)

                Timer.log_stats()

            stats_file_name = self.get_diff_stats_file(difficulty)
            with open(stats_file_name, "wb") as f:
                pickle.dump(stats_map_diff, f)

        all_stats_file_name = self.get_diff_stats_file()
        with open(all_stats_file_name, "wb") as f:
            pickle.dump(self.stats_map, f)

        self.save_stats("matching_after_{}".format(self.cache_map[Property.all_combinations]))
        self.log()
        # These two are different approaches to stats
        evaluate_all_matching_stats(self.stats_map, n_examples=30)
        evaluate_stats(self.stats)

    def save_stats(self, key=""):
        file_name = "{}/stats_{}_{}.pkl".format(self.output_dir, key, get_tmsp())
        with open(file_name, "wb") as f:
            pickle.dump(self.stats, f)
            print("stats saved")


def get_tmsp():
    now = datetime.now()
    return now.strftime("%Y_%m_%d_%H_%M_%S_%f")


def append_all(pipeline, str):
    use_degensac = "DEGENSAC" if pipeline.use_degensac else "RANSAC"
    estimate_K = "estimatedK" if pipeline.estimate_k else "GTK"
    rectified = "rectified" if pipeline.rectify else "unrectified"
    timestamp = get_tmsp()
    return "{}_{}_{}_{}_{}_{}_{}".format(str, pipeline.scene_type, pipeline.scene_name.replace("/", "_"), use_degensac, estimate_K, rectified, timestamp)


def main():

    parser = argparse.ArgumentParser(prog='pipeline')
    parser.add_argument('--output_dir', help='output dir')
    args = parser.parse_args()

    Timer.start()

    pipeline, config_map = Pipeline.configure("config.txt", args)
    all_configs = CartesianConfig.get_configs(config_map)
    for config, cache_map in all_configs:
        pipeline.config = config
        pipeline.cache_map = cache_map
        pipeline.run()

    Timer.log_stats()


if __name__ == "__main__":
    main()
