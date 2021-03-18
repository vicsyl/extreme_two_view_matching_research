from scene_info import SceneInfo
from depth_to_normals import compute_normals_simple_diff_convolution, get_megadepth_file_names_and_dir, megadepth_input_dir
from dataclasses import dataclass
from rectification import possibly_upsample_normals, get_rectified_keypoints
from connected_components import get_connected_components, show_components
from utils import Timer
from pathlib import Path
from matching import match_images_and_keypoints
from evaluation import compare_poses, Stats
from config import Config

import numpy as np
import cv2 as cv


@dataclass
class Pipeline:

    scene_name: str

    sequential_files_limit: int
    chosen_depth_files: int

    save_normals: bool
    normals_dir: str

    #matching
    feature_descriptor: cv.Feature2D
    matching_dir: str
    matching_difficulties: list
    matching_limit: str

    def __post_init__(self):
        self.scene_info = SceneInfo.read_scene(self.scene_name)
        self.show_clustered_components = True
        self.depth_input_dir = megadepth_input_dir(self.scene_name)

    def process_file(self, img_name):

        Timer.start_check_point("processing img")
        print("Processing: {}".format(img_name))
        # TODO skip/override existing (on multiple levels)

        # input image & K
        img_file_path = self.scene_info.get_img_file_path(img_name)
        img = cv.imread(img_file_path, None)
        K = self.scene_info.get_img_K(img_name)

        if Config.rectify():

            # depth => indices
            output_directory = "{}/{}".format(self.normals_dir, img_name)
            normals, normal_indices = compute_normals_simple_diff_convolution(self.scene_info, self.depth_input_dir, "{}.npy".format(img_name), self.save_normals, output_directory)
            # TODO - shouldn't the normals be persisted already with the connected components?

            # normal indices => cluster indices (maybe safe here?)
            normal_indices = possibly_upsample_normals(img, normal_indices)
            components_indices, valid_components_dict = get_connected_components(normal_indices, range(len(normals)), True)
            if self.show_clustered_components:
                show_components(components_indices, valid_components_dict.keys())

            # get rectification
            kps, descs = get_rectified_keypoints(normals, components_indices, valid_components_dict, img, K, descriptor=self.feature_descriptor, img_name=img_name)
        else:
            kps, descs = self.feature_descriptor.detectAndCompute(img, None)

        Timer.end_check_point("processing img")
        return img, K, kps, descs

    def run_sequential_pipeline(self):

        file_names, _ = get_megadepth_file_names_and_dir(self.scene_name, self.sequential_files_limit, self.chosen_depth_files)
        for depth_data_file_name in file_names:
            self.process_file(depth_data_file_name[:-4])

    def run_matching_pipeline(self):

        processed_pairs = 0
        for difficulty in self.matching_difficulties:

            print("Difficulty: {}".format(difficulty))

            for img_pair in self.scene_info.img_pairs[difficulty]:

                Timer.start_check_point("complete image pair matching")

                if self.matching_limit is not None and processed_pairs >= self.matching_limit:
                    break

                out_dir = "work/{}/matching/{}/{}_{}".format(self.scene_info.name, self.matching_dir, img_pair.img1, img_pair.img2)

                img1, K_1, kps1, descs1 = self.process_file(img_pair.img1)
                if img1 is None:
                    print("{} couldn't be processed, skipping the matching pair {}_{}".format(img_pair.img1, img_pair.img1, img_pair.img2))
                    continue

                img2, K_2, kps2, descs2 = self.process_file(img_pair.img2)
                if img2 is None:
                    print("{} couldn't be processed, skipping the matching pair {}_{}".format(img_pair.img2, img_pair.img1, img_pair.img2))
                    continue

                Path(out_dir).mkdir(parents=True, exist_ok=True)

                E, inlier_mask, src_pts, dst_pts, kps1, kps2, tentative_matches = match_images_and_keypoints(img1, kps1, descs1, K_1, img2, kps2, descs2, K_2, self.scene_info.img_info_map, img_pair, out_dir, show=True, save=True)
                error_R, error_T = compare_poses(E, img_pair, self.scene_info, src_pts, dst_pts)
                inliers = np.sum(np.where(inlier_mask[:, 0] == [1], 1, 0))
                stats = Stats(error_R=error_R, error_T=error_T, tentative_matches=tentative_matches, inliers=inliers, all_features_1=len(src_pts), all_features_2=len(dst_pts))
                stats.save("{}/stats.txt".format(out_dir))

                #new_stats = Stats.read_from_file("{}/stats.txt".format(out_dir))
                # I can now continue with processing stats over the iterated data

                processed_pairs = processed_pairs + 1
                Timer.end_check_point("complete image pair matching")


def main():

    Timer.start()

    pipeline = Pipeline(scene_name="scene2",
                        sequential_files_limit=10,
                        chosen_depth_files=None,
                        save_normals=True,
                        matching_dir="pipeline_with_rectification_foo",
                        matching_difficulties=[0],
                        matching_limit=1,
                        feature_descriptor=cv.SIFT_create(),
                        normals_dir="scene2/normals/simple_diff_mask")

    #pipeline.run_sequential_pipeline()
    pipeline.run_matching_pipeline()

    Timer.end()


if __name__ == "__main__":
    main()
