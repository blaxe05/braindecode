import numpy as np
from numpy.random import RandomState

from braindecode.util import get_balanced_batches


class CropsFromTrialsIterator(object):
    """
    Iterator sampling crops out the trials so that each sample 
    (after receptive size of the ConvNet) in each trial is predicted.
    
    Predicting the given input batches can lead to some samples
    being predicted multiple times, if the receptive field size
    (input_time_length - n_preds_per_input + 1) is not a divisor
    of the trial length.  :func:`compute_preds_per_trial_from_crops`
    can help with removing the overlapped predictions again for evaluation.

    Parameters
    ----------
    batch_size: int
    input_time_length: int
        Input time length of the ConvNet, determines size of batches in
        3rd dimension.
    n_preds_per_input: int
        Number of predictions ConvNet makes per one input. Can be computed
        by making a forward pass with the given input time length, the
        output length in 3rd dimension is n_preds_per_input.
    seed: int
        Random seed for initialization of `numpy.RandomState` random generator
        that shuffles the batches.
    
    See Also
    --------
    braindecode.experiments.monitors.compute_preds_per_trial_from_crops : Assigns predictions to trials, removes overlaps.
    """

    def __init__(
        self,
        batch_size,
        input_time_length,
        n_preds_per_input,
        seed=(2017, 6, 28),
    ):
        self.batch_size = batch_size
        self.input_time_length = input_time_length
        self.n_preds_per_input = n_preds_per_input
        self.seed = seed
        self.rng = RandomState(self.seed)

    def reset_rng(self):
        self.rng = RandomState(self.seed)

    def get_batches(self, dataset, shuffle):
        # start always at first predictable sample, so
        # start at end of receptive field
        n_receptive_field = self.input_time_length - self.n_preds_per_input + 1
        i_trial_starts = [n_receptive_field - 1] * len(dataset.X)
        i_trial_stops = [trial.shape[1] for trial in dataset.X]

        # Check whether input lengths ok
        input_lens = i_trial_stops
        for i_trial, input_len in enumerate(input_lens):
            assert input_len >= self.input_time_length, (
                "Input length {:d} of trial {:d} is smaller than the "
                "input time length {:d}".format(
                    input_len, i_trial, self.input_time_length
                )
            )
        start_stop_blocks_per_trial = _compute_start_stop_block_inds(
            i_trial_starts,
            i_trial_stops,
            self.input_time_length,
            self.n_preds_per_input,
            check_preds_smaller_trial_len=True,
        )
        for i_trial, trial_blocks in enumerate(start_stop_blocks_per_trial):
            assert trial_blocks[0][0] == 0
            assert trial_blocks[-1][1] == i_trial_stops[i_trial]

        return self._yield_block_batches(
            dataset.X, dataset.y, start_stop_blocks_per_trial, shuffle=shuffle
        )

    def _yield_block_batches(self, X, y, start_stop_blocks_per_trial, shuffle):
        # add trial nr to start stop blocks and flatten at same time
        i_trial_start_stop_block = [
            (i_trial, start, stop)
            for i_trial, block in enumerate(start_stop_blocks_per_trial)
            for (start, stop) in block
        ]
        i_trial_start_stop_block = np.array(i_trial_start_stop_block)
        if i_trial_start_stop_block.ndim == 1:
            i_trial_start_stop_block = i_trial_start_stop_block[None, :]

        blocks_per_batch = get_balanced_batches(
            len(i_trial_start_stop_block),
            batch_size=self.batch_size,
            rng=self.rng,
            shuffle=shuffle,
        )
        for i_blocks in blocks_per_batch:
            start_stop_blocks = i_trial_start_stop_block[i_blocks]
            batch = _create_batch_from_i_trial_start_stop_blocks(
                X, y, start_stop_blocks, self.n_preds_per_input
            )
            yield batch


def _compute_start_stop_block_inds(
    i_trial_starts,
    i_trial_stops,
    input_time_length,
    n_preds_per_input,
    check_preds_smaller_trial_len,
):
    """
    Compute start stop block inds for all trials
    Parameters
    ----------
    i_trial_starts: 1darray/list of int
        Indices of first samples to predict(!).
    i_trial_stops: 1darray/list of int
        Indices one past last sample to predict.
    input_time_length: int
    n_preds_per_input: int
    check_preds_smaller_trial_len: bool
        Check whether predictions fit inside trial
    Returns
    -------
    start_stop_blocks_per_trial: list of list of (int, int)
        Per trial, a list of 2-tuples indicating start and stop index
        of the inputs needed to predict entire trial.
    """
    # create start stop indices for all batches still 2d trial -> start stop
    start_stop_blocks_per_trial = []
    for i_trial in range(len(i_trial_starts)):
        i_trial_start = i_trial_starts[i_trial]
        i_trial_stop = i_trial_stops[i_trial]
        start_stop_blocks = _get_start_stop_blocks_for_trial(
            i_trial_start, i_trial_stop, input_time_length, n_preds_per_input
        )

        if check_preds_smaller_trial_len:
            # check that block is correct, all predicted samples together
            # should be the trial samples
            all_predicted_samples = [
                range(stop - n_preds_per_input, stop)
                for _, stop in start_stop_blocks
            ]
            # this check takes about 50 ms in performance test
            # whereas loop itself takes only 5 ms.. deactivate it if not necessary
            assert np.array_equal(
                range(i_trial_starts[i_trial], i_trial_stops[i_trial]),
                np.unique(np.concatenate(all_predicted_samples)),
            )

        start_stop_blocks_per_trial.append(start_stop_blocks)
    return start_stop_blocks_per_trial


def _get_start_stop_blocks_for_trial(
    i_trial_start, i_trial_stop, input_time_length, n_preds_per_input
):

    """
    Compute start stop block inds for one trial
    Parameters
    ----------
    i_trial_start:  int
        Index of first sample to predict(!).
    i_trial_stops: 1daray/list of int
        Index one past last sample to predict.
    input_time_length: int
    n_preds_per_input: int
    Returns
    -------
    start_stop_blocks: list of (int, int)
        A list of 2-tuples indicating start and stop index
        of the inputs needed to predict entire trial.
    """
    start_stop_blocks = []
    i_window_stop = i_trial_start  # now when we add sample preds in loop,
    # first sample of trial corresponds to first prediction
    while i_window_stop < i_trial_stop:
        i_window_stop += n_preds_per_input
        i_adjusted_stop = min(i_window_stop, i_trial_stop)
        i_window_start = i_adjusted_stop - input_time_length
        start_stop_blocks.append((i_window_start, i_adjusted_stop))

    return start_stop_blocks


def _create_batch_from_i_trial_start_stop_blocks(
    X, y, i_trial_start_stop_block, n_preds_per_input=None
):
    Xs = []
    ys = []
    for i_trial, start, stop in i_trial_start_stop_block:
        Xs.append(X[i_trial][:, start:stop])
        if not hasattr(y[i_trial], "__len__"):
            ys.append(y[i_trial])
        else:
            assert n_preds_per_input is not None
            ys.append(y[i_trial][stop - n_preds_per_input : stop])
    batch_X = np.array(Xs)
    batch_y = np.array(ys)
    # add empty fourth dimension if necessary
    if batch_X.ndim == 3:
        batch_X = batch_X[:, :, :, None]
    return batch_X, batch_y
