import matplotlib.pyplot as plt
import torch
import math
from dataclasses import dataclass
from matplotlib.patches import Circle


@dataclass
class CoveringParams:

    r_max: float
    t_max: float
    ts_opt: list
    phis_opt: list

    @staticmethod
    def get_log_1_8_covering():
        return CoveringParams(
            r_max=1.8,
            t_max=6.0,
            ts_opt=[2.88447, 6.2197],
            phis_opt=[math.pi / 8.0, math.pi / 16.0])

    @staticmethod
    def light_covering():
        return CoveringParams(
            r_max=1.8,
            t_max=6.0,
            # TODO just pretty randomly populated
            ts_opt=[2.2, 2.88447, 4.28, 6.2197],
            phis_opt=[math.pi / 8.0, math.pi / 10.0, math.pi / 12.0, math.pi / 16.0])

    @staticmethod
    def denser_covering_backup():
        return CoveringParams(
            r_max=1.8,
            t_max=6.0,
            # TODO just pretty randomly populated
            ts_opt=[2.2, 2.5, 2.88447, 3.5, 4.28, 5.5, 6.2197],
            phis_opt=[math.pi / 16.0, math.pi / 20.0, math.pi / 24.0, math.pi / 28.0, math.pi / 32.0, math.pi / 36.0, math.pi / 40.0])

    @staticmethod
    def denser_covering():
        return CoveringParams(
            r_max=1.8,
            t_max=6.0,
            # TODO just pretty randomly populated
            ts_opt=[2.2, 2.5],
            phis_opt=[math.pi / 16.0, math.pi / 20.0])

    def covering_coordinates(self):
        t_phi_list = []
        for index, t_opt in enumerate(self.ts_opt):
            for phi in torch.arange(start=0.0, end=math.pi, step=self.phis_opt[index]):
                t_phi_list.append((t_opt, phi))

        return torch.tensor(t_phi_list).T


def distance_matrix(t1, t2, phi1, phi2):
    """
    t1, t2 tilts, not their logs!!
    """
    t1 = t1.unsqueeze(1).expand(-1, t2.shape[0])
    phi1 = phi1.unsqueeze(1).expand(-1, phi2.shape[0])
    t2 = t2.unsqueeze(0).expand(t1.shape[0], -1)
    phi2 = phi2.unsqueeze(0).expand(phi1.shape[0], -1)
    dist = (t1 / t2 + t2 / t1) * torch.cos(phi1 - phi2) ** 2 + (t1 * t2 + 1.0 / t2 * t1) * torch.sin(phi1 - phi2) ** 2
    dist = dist / 2
    return dist


def draw_identity_data(ax, data, r):

    r = math.log(r)
    data_around_identity_mask = data[0] < math.exp(r)
    in_data = data[:, data_around_identity_mask]
    opt_conv_draw(ax, in_data, 'c', 2)


def vote(centers, data, r, fraction_th, iter_th):

    r = math.log(r)
    rhs = (math.exp(2 * r) + 1) / (2 * math.exp(r))

    data_around_identity_mask = data[0] < math.exp(r)
    filtered_data = data[:, ~data_around_identity_mask]

    iter_finished = 0
    winning_centers = []
    rect_fraction = 1 - filtered_data.shape[1] / data.shape[1]
    print("rect_fraction: {}".format(rect_fraction))
    while rect_fraction < fraction_th and iter_finished < iter_th:
        print("rect_fraction: {}".format(rect_fraction))

        distances = distance_matrix(centers[0], filtered_data[0], centers[1], filtered_data[1])
        votes = (distances < rhs)
        votes_count = votes.sum(axis=1)
        sorted, indices = torch.sort(votes_count, descending=True)

        data_in_mask = votes[indices[0]]
        #data_in = filtered_data[:, data_in_mask]
        #draw(ax, data_in, 'y', 2)

        filtered_data = filtered_data[:, ~data_in_mask]
        rect_fraction = 1 - filtered_data.shape[1] / data.shape[1]

        winning_center = centers[:, indices[0]]
        winning_centers.append((winning_center[0].item(), winning_center[1].item()))
        iter_finished += 1

    return torch.tensor(winning_centers)


def opt_conv_draw(ax, ts_phis, color, size):

    tilts_logs = torch.log(ts_phis[0])
    xs = torch.cos(ts_phis[1]) * tilts_logs
    ys = torch.sin(ts_phis[1]) * tilts_logs
    ax.plot(xs, ys, 'o', color=color, markersize=size)


def opt_cov_prepare_plot(cov_params: CoveringParams):
    fig, ax = plt.subplots()
    plt.title("Nearest neighbors")

    log_max_radius = math.log(cov_params.t_max)
    log_unit_radius = math.log(cov_params.r_max)

    ax.set_aspect(1.0)

    factor = 1.2
    ax.set_xlim((-factor * log_max_radius, factor * log_max_radius))
    ax.set_ylim((-factor * log_max_radius, factor * log_max_radius))

    circle = Circle((0, 0), log_max_radius, color='r', fill=False)
    ax.add_artist(circle)
    circle = Circle((0, 0), log_unit_radius, color='r', fill=False)
    ax.add_artist(circle)

    return ax


def draw_in_center(ax, center, data, r_max):
    r_log = math.log(r_max)
    rhs = (math.exp(2 * r_log) + 1) / (2 * math.exp(r_log))
    distances = distance_matrix(center[0, None], data[0], center[1, None], data[1])
    votes = (distances[0] < rhs)
    data_in = data[:, votes]
    opt_conv_draw(ax, data_in, 'y', 2)


def main_demo():

    covering_params = CoveringParams.denser_covering()

    data_count = 5000
    data = torch.rand(2, data_count)
    data[0] = torch.abs(data[0] * 5.0 + 1)
    data[1] = data[1] * math.pi

    ax = opt_cov_prepare_plot(covering_params)
    opt_conv_draw(ax, data, "b", 1.0)
    covering_coords = covering_params.covering_coordinates()
    opt_conv_draw(ax, covering_coords, "r", 3.0)

    winning_centers = vote(covering_coords, data, covering_params.r_max, fraction_th=0.6, iter_th=30)

    for i, wc in enumerate(winning_centers):
        draw_in_center(ax, wc, data, covering_params.r_max)
        opt_conv_draw(ax, wc, "b", 5.0)

    draw_identity_data(ax, data, covering_params.r_max)

    plt.show()


if __name__ == "__main__":
    main_demo()