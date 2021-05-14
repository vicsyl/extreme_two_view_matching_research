import torch
import math
from utils import Timer

# https://web.archive.org/web/20120107030109/http://cgafaq.info/wiki/Evenly_distributed_points_on_sphere#Spirals
def n_points_across_sphere(N):

    s = 3.6 / math.sqrt(N)
    dz = 2.0 / N
    longitude = 0
    z = 1 - dz / 2

    points = torch.zeros((N, 3))
    for k in range(N):
        r = math.sqrt(1 - z * z)
        points[k] = torch.tensor([math.cos(longitude) * r, math.sin(longitude) * r, z])
        z = z - dz
        longitude = longitude + s / r

    return points


def cluster(normals: torch.Tensor, filter_mask, max_iter=20):

    # TODO - move to n_points_across_sphere?
    n = 300
    timer_label = "1st phase of clustering for N={}".format(n)
    Timer.start_check_point(timer_label)
    n_centers = n_points_across_sphere(n)

    def expand_centers_get_sums(centers):

        centers = centers.expand(normals.shape[0], normals.shape[1], -1, -1)
        centers = centers.permute(2, 0, 1, 3)

        diffs = centers[:] - normals
        diff_norm = torch.norm(diffs, dim=3)

        near_ones_per_cluster_center = torch.where(diff_norm < distance_threshold, 1, 0)
        near_ones_per_cluster_center = torch.logical_and(near_ones_per_cluster_center, filter_mask)

        sums = near_ones_per_cluster_center.sum(dim=(1, 2))
        return centers, sums

    n_centers, sums = expand_centers_get_sums(n_centers)

    sortd = torch.sort(sums, descending=True)

    cluster_centers = []
    points_list = []

    max_clusters = 3
    for index, points in zip(sortd[1], sortd[0]):
        if len(cluster_centers) >= max_clusters:
            break
        if points < 20000:
            break

        distance_ok = True
        for cluster_center in cluster_centers:
            diff = n_centers[index, 0, 0] - cluster_center[0, 0, 0]
            diff_norm = torch.norm(diff)
            if diff_norm < distance_threshold_angle_times_two:
                distance_ok = False
                break

        if distance_ok:
            cluster_centers.append(n_centers[index].clone().unsqueeze(dim=0))
            points_list.append(points)

    if len(cluster_centers) == 1:
        cluster_centers = cluster_centers[0]
    else:
        cluster_centers = torch.cat(cluster_centers)

    cluster_centers_old = cluster_centers.clone()
    for i in range(cluster_centers_old.shape[0] - 1):
        for j in range(i + 1, cluster_centers_old.shape[0]):
            angle = math.acos(cluster_centers_old[i, 0, 0] @ cluster_centers_old[j, 0, 0].T)
            angle_degrees = 180 / math.pi * angle
            print("angle between normal {} and {}: {} degrees".format(i, j, angle_degrees))

    Timer.end_check_point(timer_label)

    timer_label2 = "2nd phase of clustering - kmeans"
    Timer.start_check_point(timer_label2)
    cluster_centers_new, arg_mins = kmeans(normals, filter_mask, clusters=None, max_iter=max_iter, cluster_centers=cluster_centers)
    Timer.end_check_point(timer_label2)

    angles = []
    for i in range(cluster_centers_new.shape[0]):
        angle = math.acos(cluster_centers_new[i] @ cluster_centers_old[i, 0, 0].T)
        angle_degrees = 180 / math.pi * angle
        angles.append(angle_degrees)
    print("Angles differences: {}".format(angles))

    _, sums = expand_centers_get_sums(cluster_centers_new)
    for i in range(cluster_centers_new.shape[0]):
        print("{}th cluster: from {} to {} points".format(i, points_list[i], sums[i]))

    return cluster_centers_new, arg_mins


initial_cluster_centers = torch.Tensor([
        [+math.sqrt(3) / 2,  0.00, -0.5],
        [-math.sqrt(3) / 4, +0.75, -0.5],
        [-math.sqrt(3) / 4, -0.75, -0.5]
    ])

# TWEAK
distance_threshold = 0.6
angle_distance = 2 * math.asin(distance_threshold / 2)
distance_threshold_angle_times_two = math.sin(angle_distance * 2 / 2) * 2
angle_distance2 = 2 * math.asin(distance_threshold_angle_times_two / 2)

# just an informative message about angle distance threshold for keeping a normal in a cluster
#print("angle distance in rad used for k_means in radians: {}".format(angle_distance))
#print("the same in degrees: {}".format(angle_distance / math.pi * 180.0))


def kmeans(normals: torch.Tensor, filter_mask, clusters=None, max_iter=20, cluster_centers=None):
    """
    :param normals: torch: w,h,3 (may add b)
    :return:
    """

    if clusters is None:
        assert cluster_centers is not None
        clusters = cluster_centers.shape[0]

    if cluster_centers is None:
        assert clusters <= 3
        shape = tuple([clusters]) + tuple(normals.shape)
        cluster_centers = torch.zeros(shape)
        for i in range(clusters):
            cluster_centers[i] = initial_cluster_centers[i]


    old_arg_mins = None
    iter = 0 # so that max_iter == 0 works
    for iter in range(max_iter):

        diffs = cluster_centers[:] - normals

        diff_norm = torch.norm(diffs, dim=3)
        mins = torch.min(diff_norm, dim=0, keepdim=True)
        arg_mins = mins[1].squeeze(0)
        filtered_arg_mins = torch.where(filter_mask, arg_mins, 3)
        filtered_arg_mins = torch.where(mins[0].squeeze(0) < distance_threshold, filtered_arg_mins, 3)

        if old_arg_mins is not None:
            changes = old_arg_mins[old_arg_mins != filtered_arg_mins].shape[0]
            if changes == 0:
                break

        old_arg_mins = filtered_arg_mins

        for i in range(clusters):
        #for i in range(1):
            cluster_i_points = normals[filtered_arg_mins == i]
            # TODO REALLY??!! - this is using just the euclidian distance!!!
            new_cluster = torch.sum(cluster_i_points, 0) / cluster_i_points.shape[0]
            new_cluster = new_cluster / torch.norm(new_cluster)
            cluster_centers[i, :, :, :] = new_cluster

    print("iter: {}".format(iter))

    diffs = cluster_centers[:] - normals
    diff_norm = torch.norm(diffs, dim=3)
    mins = torch.min(diff_norm, dim=0, keepdim=True)
    arg_mins = mins[1].squeeze(0)
    filtered_arg_mins = torch.where(filter_mask == 1, arg_mins, 3)
    mins = mins[0].squeeze(0)

    arg_mins = torch.where(mins < distance_threshold, filtered_arg_mins, 3)
    #print_and_get_stats(arg_mins)

    ret = cluster_centers[:, 0, 0, :], arg_mins
    return ret


def print_and_get_stats(arg_mins):
    stats = [None] * 4
    for i in range(4):
        stats[i] = torch.where(arg_mins == i, 1, 0).sum().item()
    print("stats: {}".format(stats))
    return stats
