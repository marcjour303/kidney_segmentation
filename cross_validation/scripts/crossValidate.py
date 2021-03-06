import sys
import os
import time

import glob
import shutil

import numpy as np

import torch
import torch.utils.data as data

#
sys.path.insert(0, "models/")

from model_resUnet import UNet

import train
import predict
import evaluate
import dataLoading

# After the slice-wise training, the validation fuses all specified imaging stations
# to a common space to calculate subject-wise Dice scores and other evaluation metrics
c_target_spacing = np.array((2.23214293, 2.23214293, 4.5)) # abdominal spacing
#c_target_spacing = np.array((2.23214293, 2.23214293, 3.)) # top station spacing

def main(argv):

    if True:
        path_network_out = "../networks/kidney_64_8fold_retrainAdded58_v7_DataLoaderFix/"

        path_training_slices = "../image_data/kidney_128/"
        path_split = "../splits/kidney_64_8fold/"

        # Paths to nrrd volumes (used during evaluation)
        path_stations_img = "/media/taro/DATA/Taro/UKBiobank/segmentations/kidney/combined_128/signals/NRRD/"
        path_stations_gt = "/media/taro/DATA/Taro/UKBiobank/segmentations/kidney/combined_128/segmentations/NRRD_v7/"

        # Select which MRI stations to use for training and evaluation
        station_ids = [1, 2]

        # Optional name of list file in split path with ids 
        # which are to be used as additional training samples, in each split.
        # Set to None for conventional cross-validation
        path_train_ids_add = "images_add58.txt"

    if False:
        path_network_out = "../networks/liver_98_traintest/"

        path_training_slices = "../image_data/liver_refined_99/"
        path_split = "../splits/liver_98_traintest/"

        # Paths to nrrd volumes (used during evaluation)
        path_stations_img = "/media/taro/DATA/Taro/UKBiobank/segmentations/liver/Andres_refined/signals/"
        path_stations_gt = "/media/taro/DATA/Taro/UKBiobank/segmentations/liver/Andres_refined/segmentations/"

        # Select which MRI stations to use for training and evaluation
        station_ids = [0, 1, 2]

        # Optional name of list file in split path with ids 
        # which are to be used as additional training samples, in each split.
        # Set to None for conventional cross-validation
        path_train_ids_add = None

    runExperiment(path_network_out, path_training_slices, path_split, path_stations_img, path_stations_gt, path_train_ids_add, station_ids)


def runExperiment(path_network_out, path_training_slices, path_split, path_stations_img, path_stations_gt, path_train_ids_add, station_ids):

    I = 80000 # Training iterations
    save_step = 5000 # Iterations between checkpoint saving
    #I = 100 # Training iterations
    #save_step = 100 # Iterations between checkpoint saving

    I_reduce_lr = 60000 # Reduce learning rate by factor 10 after this many iterations

    channel_count = 3 # Number of input channels
    class_count = 2 # Number of labels, including background
    class_weights = torch.FloatTensor([1, 1]) # Background, L1, L2...

    start_k = 0 # First cross-validation set to train and validate against

    do_train = True
    do_predict = True

    # Create folders
    if do_train and start_k == 0 and os.path.exists(path_network_out):
        print("ABORT: Network path already exists!")
        sys.exit()
        #shutil.rmtree(path_network_out)

    # Create folders and documentation when starting from scratch
    if do_train and start_k == 0:
        os.makedirs(path_network_out)
        createDocumentation(path_network_out, path_split)

    # Parse split
    split_files = [f for f in os.listdir(path_split) if os.path.isfile(os.path.join(path_split, f))]
    split_files = [f for f in split_files if "images_set" in f]

    K = len(split_files)
    cv_subsets = np.arange(K)

    #
    for k in range(start_k, K):
    #for k in range(start_k, 1):

        # Validate against subset k
        val_subset = cv_subsets[k]

        # Train on all but subset k
        train_subsets = [x for f,x in enumerate(cv_subsets) if f != k]

        print("########## Validating against subset {}".format(val_subset))

        #
        path_out_k = path_network_out + "subset_{}/".format(val_subset)
        path_checkpoints = path_out_k + "snapshots/"

        if do_train or do_predict:
            print("Initializing network...")
            net = UNet(channel_count, class_count).cuda()

        if do_train:

            os.makedirs(path_out_k)
            os.makedirs(path_checkpoints)

            loader_train = getDataloader(path_training_slices + "data/", path_out_k + "train_files.txt", train_subsets, path_split, B=1, sigma=2, points=8, path_train_ids_add=path_train_ids_add, station_ids=station_ids)
            time = train.train(net, loader_train, I, path_checkpoints, save_step, class_weights, I_reduce_lr)

            with open(path_out_k + "training_time.txt", "w") as f: f.write("{}".format(time))

        if do_predict:
            evaluate.evaluateSnapshots(path_checkpoints, path_stations_img, path_stations_gt, path_split, val_subset, path_out_k + "eval/", net, station_ids, c_target_spacing)

        evaluate.writeSubsetTrainingCurve(path_out_k)
        
    evaluate.aggregate(path_network_out, I, save_step)


# Copy scripts to network project folder as documentation
def createDocumentation(network_path, split_path):

    os.makedirs(network_path + "documentation")
    for file in glob.glob("*.py"): shutil.copy(file, network_path + "documentation/")

    os.makedirs(network_path + "split")
    for file in glob.glob(split_path + "*"): shutil.copy(file, network_path + "split/")


#
def getDataloader(input_path, output_path, subsets, path_split, B, sigma, points, path_train_ids_add, station_ids):

    # Get chosen volumes
    subject_ids = []
    for k in subsets:
        subset_file = path_split + "images_set_{}.txt".format(k)

        with open(subset_file) as f: entries = f.readlines()
        subject_ids.extend(entries)

    # Add optional training images if specified
    if not path_train_ids_add is None:
        with open(path_split + path_train_ids_add) as f: entries = f.readlines()
        subject_ids.extend(entries)

    subject_ids = [f.replace("\n","") for f in subject_ids]    

    print("Loading data for {} subjects".format(len(subject_ids)))

    # For each subject, use the specified stations
    stations = []
    for s in station_ids:
        stations.extend([f + "_station{}".format(s) for f in subject_ids])

    # Get training samples
    files = [f for f in os.listdir(input_path) if os.path.isfile(os.path.join(input_path, f))]
    files = [f for f in files if f.split("_slice")[0] in stations]

    paths_seg = [input_path + f for f in files if "seg.npy" in f]
    paths_img = [f.replace("seg.npy", "img.npy") for f in paths_seg]

    print("Found {} samples...".format(len(paths_img)))

    #
    dataset = dataLoading.SliceDatasetDeformable(paths_img, paths_seg, sigma, points)

    loader = torch.utils.data.DataLoader(dataset,
                                        num_workers=8,
                                        batch_size=B,
                                        shuffle=True,
                                        pin_memory=True,
                                        # use different random seeds for each worker
                                        # courtesy of https://github.com/xingyizhou/CenterNet/issues/233
                                        worker_init_fn = lambda id: np.random.seed(torch.initial_seed() // 2**32 + id) )

    # Document actually used training files
    with open(output_path, "w") as f:
        for img_file in paths_img:
            f.write("{}\n".format(img_file))
            f.write("{}\n".format(img_file.replace("img.npy", "seg.npy")))

    return loader


if __name__ == '__main__':
    main(sys.argv)
