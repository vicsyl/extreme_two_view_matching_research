# SVP Project

## Paper

* https://arxiv.org/pdf/2008.09497.pdf

## Depth estimating CNNs

### MegaDepth

* https://github.com/zhengqili/MegaDepth
* preprocessing: rescaling the images to have a maximum dimension of 512, with the other
dimension chosen as the multiple of 32 that best preserves the original aspect ratio (as in the original paper)
 (only now!!!)
* poor estimation of sky depth
* explore the range of the depth values!!! 

### MonoDepth2

* https://github.com/nianticlabs/monodepth2
* rescales the image exactly according to the model (will be using 640x192 as it's closest to the resolution used in MegaDepth)
* is supposed to be estimating sky better, but ... let's see
* doesn't seem that good from the pictures
* min/max depth bounds (not tight!!!)
* TODO depth data generated, but they are still to be tried on the downstream processing 

## Normals from depth

* PROBLEM - the depths are given up to a scale - which affects the normals, also the ranges differ across the CNNs

## Normals through simple differential conv mask

* improvement TODO - adjust according to the projecting rays 
* similar are normals through sobel conv mask

## Normals through fitting a plane

* PROBLEM - seems to be to slow, but I can still try torch.unfold to parallelize it and speed it up


## Clustering normals

* spherical k-means
* GOOD filtering the normals which differ more than threshold angle (80 degrees according to the original paper) seems handle the sky pretty neatly
* various possibilities, how to improve it / speed it up
* TODO I think it would be beneficial to decide automatically on the number of the clusters (i.e. dominating planes), at least between 2 and 3


## Rectification

* generally seems to be working well, but
* PROBLEM: probably due to some rounding error the keypoints are back mapped to invalid positions 
(it can also be the desriptor finding keypoints at/beyond the border of the original img when mapped with the homography)
* PROBLEM: sometimes the homography warps the image to a much bigger image which makes the program hang
* needs to be solved by restricting the area which need to be warped (I don't see how to do it via cv2.warpPerspective, though)
* it really seems to be finding more inlier matches   


## Matching

* pretty straighforward, need to plug in different descriptors


## Evaluation

* adopted the evaluation of pose (essential matrix) estimation from https://github.com/ducha-aiki/ransac-tutorial-2020-data
* on first sight the estimation errors look reasonable


# Others

* the pipeline doesn't seem to be very fast
* I think I also need to start computing on the CMP cluster 
