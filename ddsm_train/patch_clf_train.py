import os, argparse, sys
import numpy as np
from keras.callbacks import (
    ReduceLROnPlateau, 
    EarlyStopping, 
    ModelCheckpoint
)
from keras.models import load_model, Model
from dm_image import DMImageDataGenerator
from dm_keras_ext import (
    get_dl_model,  
    load_dat_ram,
    do_3stage_training,
    DMFlush
)
from dm_multi_gpu import make_parallel
import warnings
import exceptions
warnings.filterwarnings('ignore', category=exceptions.UserWarning)
import keras.backend as K
dim_ordering = K.image_dim_ordering()
# if K.backend() == 'tensorflow':
#     import tensorflow as tf
#     config = tf.ConfigProto()
#     config.gpu_options.per_process_gpu_memory_fraction = 0.7



def run(train_dir, val_dir, test_dir,
        img_size=[256, 256], img_scale=255., 
        featurewise_center=True, featurewise_mean=59.6, 
        equalize_hist=True, augmentation=False,
        class_list=['background', 'malignant', 'benign'],
        batch_size=64, train_bs_multiplier=.5, nb_epoch=5, 
        top_layer_epochs=10, all_layer_epochs=20,
        load_val_ram=False, load_train_ram=False,
        net='resnet50', use_pretrained=True,
        nb_init_filter=32, init_filter_size=5, init_conv_stride=2, 
        pool_size=2, pool_stride=2, 
        weight_decay=.0001, weight_decay2=.0001, bias_multiplier=.1, 
        alpha=.0001, l1_ratio=.0, 
        inp_dropout=.0, hidden_dropout=.0, hidden_dropout2=.0, 
        optim='sgd', init_lr=.01, lr_patience=10, es_patience=25,
        resume_from=None, auto_batch_balance=False, 
        pos_cls_weight=1.0, neg_cls_weight=1.0,
        top_layer_nb=None, top_layer_multiplier=.1, all_layer_multiplier=.01,
        best_model='./modelState/patch_clf.h5',
        final_model="NOSAVE"):
    '''Train a deep learning model for patch classifications
    '''

    # ======= Environmental variables ======== #
    random_seed = int(os.getenv('RANDOM_SEED', 12345))
    nb_worker = int(os.getenv('NUM_CPU_CORES', 4))
    gpu_count = int(os.getenv('NUM_GPU_DEVICES', 1))

    # ========= Image generator ============== #
    # if use_pretrained:  # use pretrained model's preprocessing.
    #     train_imgen = DMImageDataGenerator()
    #     val_imgen = DMImageDataGenerator()
    if featurewise_center:
        # fitgen = DMImageDataGenerator()
        # # Calculate pixel-level mean and std.
        # print "Create generator for mean and std fitting"
        # fit_patch_generator = fitgen.flow_from_directory(
        #     train_dir, target_size=img_size, target_scale=img_scale,
        #     classes=class_list, class_mode=None, batch_size=batch_size,
        #     shuffle=True, seed=random_seed)
        # sys.stdout.flush()
        # fit_X_lst = []
        # patches_seen = 0
        # while patches_seen < fit_size:
        #     X = fit_patch_generator.next()
        #     fit_X_lst.append(X)
        #     patches_seen += len(X)
        # fit_X_arr = np.concatenate(fit_X_lst)
        train_imgen = DMImageDataGenerator(featurewise_center=True)
            # featurewise_std_normalization=True)
        val_imgen = DMImageDataGenerator(featurewise_center=True)
        test_imgen = DMImageDataGenerator(featurewise_center=True)
            # featurewise_std_normalization=True)
        # train_imgen.fit(fit_X_arr)
        # print "Found mean=%.2f, std=%.2f" % (train_imgen.mean, train_imgen.std)
        # sys.stdout.flush()
        train_imgen.mean = featurewise_mean
        val_imgen.mean = featurewise_mean
        test_imgen.mean = featurewise_mean
        # del fit_X_arr, fit_X_lst
    else:
        train_imgen = DMImageDataGenerator()
        val_imgen = DMImageDataGenerator()
        test_imgen = DMImageDataGenerator()
        # train_imgen = DMImageDataGenerator(
        #     samplewise_center=True,
        #     samplewise_std_normalization=True)
        # val_imgen = DMImageDataGenerator(
        #     samplewise_center=True,
        #     samplewise_std_normalization=True)

    # Add augmentation options.
    if augmentation:
        train_imgen.horizontal_flip=True 
        train_imgen.vertical_flip=True
        train_imgen.rotation_range = 45.
        train_imgen.shear_range = np.pi/8.

    # ================= Model creation ============== #
    model, preprocess_input, top_layer_nb = get_dl_model(
        net, nb_class=len(class_list), use_pretrained=use_pretrained,
        resume_from=resume_from, img_size=img_size, top_layer_nb=top_layer_nb,
        weight_decay=weight_decay, bias_multiplier=bias_multiplier,
        hidden_dropout=hidden_dropout, 
        nb_init_filter=nb_init_filter, init_filter_size=init_filter_size, 
        init_conv_stride=init_conv_stride, pool_size=pool_size, 
        pool_stride=pool_stride, alpha=alpha, l1_ratio=l1_ratio, 
        inp_dropout=inp_dropout)
    if featurewise_center:
        preprocess_input = None
    if gpu_count > 1:
        model, org_model = make_parallel(model, gpu_count)
    else:
        org_model = model

    # ============ Train & validation set =============== #
    train_bs = int(batch_size*train_bs_multiplier)
    if use_pretrained:
        dup_3_channels = True
    else:
        dup_3_channels = False
    if load_train_ram:
        raw_imgen = DMImageDataGenerator()
        print "Create generator for raw train set"
        raw_generator = raw_imgen.flow_from_directory(
            train_dir, target_size=img_size, target_scale=img_scale, 
            equalize_hist=equalize_hist, dup_3_channels=dup_3_channels,
            classes=class_list, class_mode='categorical', 
            batch_size=train_bs, shuffle=False)
        print "Loading raw train set into RAM.",
        sys.stdout.flush()
        raw_set = load_dat_ram(raw_generator, raw_generator.nb_sample)
        print "Done."; sys.stdout.flush()
        print "Create generator for train set"
        train_generator = train_imgen.flow(
            raw_set[0], raw_set[1], batch_size=train_bs, 
            auto_batch_balance=auto_batch_balance, preprocess=preprocess_input, 
            shuffle=True, seed=random_seed)
    else:
        print "Create generator for train set"
        train_generator = train_imgen.flow_from_directory(
            train_dir, target_size=img_size, target_scale=img_scale,
            equalize_hist=equalize_hist, dup_3_channels=dup_3_channels,
            classes=class_list, class_mode='categorical', 
            auto_batch_balance=auto_batch_balance, batch_size=train_bs, 
            preprocess=preprocess_input, shuffle=True, seed=random_seed)
    # import pdb; pdb.set_trace()

    print "Create generator for val set"
    validation_set = val_imgen.flow_from_directory(
        val_dir, target_size=img_size, target_scale=img_scale,
        equalize_hist=equalize_hist, dup_3_channels=dup_3_channels,
        classes=class_list, class_mode='categorical', 
        batch_size=batch_size, preprocess=preprocess_input, shuffle=False)
    sys.stdout.flush()
    if load_val_ram:
        print "Loading validation set into RAM.",
        sys.stdout.flush()
        validation_set = load_dat_ram(validation_set, validation_set.nb_sample)
        print "Done."; sys.stdout.flush()

    # ==================== Model training ==================== #
    # Callbacks and class weight.
    early_stopping = EarlyStopping(monitor='val_loss', patience=es_patience, 
                                   verbose=1)
    checkpointer = ModelCheckpoint(best_model, monitor='val_acc', verbose=1, 
                                   save_best_only=True)
    stdout_flush = DMFlush()
    callbacks = [early_stopping, checkpointer, stdout_flush]
    if optim == 'sgd':
        reduce_lr = ReduceLROnPlateau(monitor='val_loss', factor=0.5, 
                                      patience=lr_patience, verbose=1)
        callbacks.append(reduce_lr)
    if auto_batch_balance:
        class_weight = None
    elif len(class_list) == 2:
        class_weight = { 0:1.0, 1:pos_cls_weight }
    elif len(class_list) == 3:
        class_weight = { 0:1.0, 1:pos_cls_weight, 2:neg_cls_weight }
    else:
        class_weight = None
    # Do 3-stage training.
    train_batches = int(train_generator.nb_sample/train_bs) + 1
    samples_per_epoch = train_bs*train_batches
    #### DEBUG ####
    # samples_per_epoch = train_bs*10
    #### DEBUG ####
    if isinstance(validation_set, tuple):
        val_samples = len(validation_set[0])
    else:
        val_samples = validation_set.nb_sample
    #### DEBUG ####
    # val_samples = 100
    #### DEBUG ####
    model, loss_hist, acc_hist = do_3stage_training(
        model, org_model, train_generator, validation_set, val_samples, 
        best_model, samples_per_epoch, top_layer_nb, net, nb_epoch=nb_epoch,
        top_layer_epochs=top_layer_epochs, all_layer_epochs=all_layer_epochs,
        use_pretrained=use_pretrained, optim=optim, init_lr=init_lr, 
        top_layer_multiplier=top_layer_multiplier, 
        all_layer_multiplier=all_layer_multiplier,
        es_patience=es_patience, lr_patience=lr_patience, 
        auto_batch_balance=auto_batch_balance, 
        pos_cls_weight=pos_cls_weight, neg_cls_weight=neg_cls_weight,
        nb_worker=nb_worker, weight_decay2=weight_decay2, 
        bias_multiplier=bias_multiplier, hidden_dropout2=hidden_dropout2)

    # Training report.
    min_loss_locs, = np.where(loss_hist == min(loss_hist))
    best_val_loss = loss_hist[min_loss_locs[0]]
    best_val_accuracy = acc_hist[min_loss_locs[0]]
    print "\n==== Training summary ===="
    print "Minimum val loss achieved at epoch:", min_loss_locs[0] + 1
    print "Best val loss:", best_val_loss
    print "Best val accuracy:", best_val_accuracy

    if final_model != "NOSAVE":
        model.save(final_model)

    # ==== Predict on test set ==== #
    print "\n==== Predicting on test set ===="
    test_generator = test_imgen.flow_from_directory(
        test_dir, target_size=img_size, target_scale=img_scale,
        equalize_hist=equalize_hist, dup_3_channels=dup_3_channels, 
        classes=class_list, class_mode='categorical', batch_size=batch_size, 
        preprocess=preprocess_input, shuffle=False)
    print "Test samples =", test_generator.nb_sample
    print "Load saved best model:", best_model + '.',
    sys.stdout.flush()
    org_model.load_weights(best_model)
    print "Done."
    test_samples = test_generator.nb_sample
    #### DEBUG ####
    # test_samples = 10
    #### DEBUG ####
    test_res = model.evaluate_generator(
        test_generator, test_samples, nb_worker=nb_worker, 
        pickle_safe=True if nb_worker > 1 else False)
    print "Evaluation result on test set:", test_res


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description="DM patch clf training")
    parser.add_argument("train_dir", type=str)
    parser.add_argument("val_dir", type=str)
    parser.add_argument("test_dir", type=str)
    parser.add_argument("--img-size", "-is", dest="img_size", nargs=2, type=int, 
                        default=[256, 256])
    parser.add_argument("--img-scale", "-ic", dest="img_scale", type=float, default=4095.)
    parser.add_argument("--featurewise-center", dest="featurewise_center", action="store_true")
    parser.add_argument("--no-featurewise-center", dest="featurewise_center", action="store_false")
    parser.set_defaults(featurewise_center=True)
    parser.add_argument("--featurewise-mean", dest="featurewise_mean", type=float, default=59.6)
    parser.add_argument("--equalize-hist", dest="equalize_hist", action="store_true")
    parser.add_argument("--no-equalize-hist", dest="equalize_hist", action="store_false")
    parser.set_defaults(equalize_hist=True)
    parser.add_argument("--batch-size", "-bs", dest="batch_size", type=int, default=64)
    parser.add_argument("--train-bs-multiplier", dest="train_bs_multiplier", type=float, default=.5)
    parser.add_argument("--augmentation", dest="augmentation", action="store_true")
    parser.add_argument("--no-augmentation", dest="augmentation", action="store_false")
    parser.set_defaults(augmentation=False)
    parser.add_argument("--class-list", dest="class_list", nargs='+', type=str, 
                        default=['background', 'malignant', 'benign'])
    parser.add_argument("--nb-epoch", "-ne", dest="nb_epoch", type=int, default=5)
    parser.add_argument("--top-layer-epochs", dest="top_layer_epochs", type=int, default=10)
    parser.add_argument("--all-layer-epochs", dest="all_layer_epochs", type=int, default=20)
    parser.add_argument("--load-val-ram", dest="load_val_ram", action="store_true")
    parser.add_argument("--no-load-val-ram", dest="load_val_ram", action="store_false")
    parser.set_defaults(load_val_ram=False)
    parser.add_argument("--load-train-ram", dest="load_train_ram", action="store_true")
    parser.add_argument("--no-load-train-ram", dest="load_train_ram", action="store_false")
    parser.set_defaults(load_train_ram=False)
    parser.add_argument("--net", dest="net", type=str, default="resnet50")
    parser.add_argument("--nb-init-filter", "-nif", dest="nb_init_filter", type=int, default=32)
    parser.add_argument("--init-filter-size", "-ifs", dest="init_filter_size", type=int, default=5)
    parser.add_argument("--init-conv-stride", "-ics", dest="init_conv_stride", type=int, default=2)
    parser.add_argument("--max-pooling-size", "-mps", dest="pool_size", type=int, default=2)
    parser.add_argument("--max-pooling-stride", "-mpr", dest="pool_stride", type=int, default=2)
    parser.add_argument("--weight-decay", "-wd", dest="weight_decay", type=float, default=.0001)
    parser.add_argument("--weight-decay2", "-wd2", dest="weight_decay2", type=float, default=.0001)
    parser.add_argument("--bias-multiplier", dest="bias_multiplier", type=float, default=.1)
    parser.add_argument("--alpha", dest="alpha", type=float, default=.0001)
    parser.add_argument("--l1-ratio", dest="l1_ratio", type=float, default=.0)
    parser.add_argument("--inp-dropout", "-id", dest="inp_dropout", type=float, default=.0)
    parser.add_argument("--hidden-dropout", "-hd", dest="hidden_dropout", type=float, default=.0)
    parser.add_argument("--hidden-dropout2", "-hd2", dest="hidden_dropout2", type=float, default=.0)
    parser.add_argument("--optimizer", dest="optim", type=str, default="sgd")
    parser.add_argument("--init-learningrate", "-ilr", dest="init_lr", type=float, default=.01)
    parser.add_argument("--lr-patience", "-lrp", dest="lr_patience", type=int, default=10)
    parser.add_argument("--es-patience", "-esp", dest="es_patience", type=int, default=25)
    parser.add_argument("--resume-from", dest="resume_from", type=str, default=None)
    parser.add_argument("--no-resume-from", dest="resume_from", action="store_const", const=None)
    parser.add_argument("--auto-batch-balance", dest="auto_batch_balance", action="store_true")
    parser.add_argument("--no-auto-batch-balance", dest="auto_batch_balance", action="store_false")
    parser.set_defaults(auto_batch_balance=False)
    parser.add_argument("--pos-cls-weight", dest="pos_cls_weight", type=float, default=1.0)
    parser.add_argument("--neg-cls-weight", dest="neg_cls_weight", type=float, default=1.0)
    parser.add_argument("--use-pretrained", dest="use_pretrained", action="store_true")
    parser.add_argument("--no-use-pretrained", dest="use_pretrained", action="store_false")
    parser.set_defaults(use_pretrained=True)
    parser.add_argument("--top-layer-nb", dest="top_layer_nb", type=int, default=None)
    parser.add_argument("--no-top-layer-nb", dest="top_layer_nb", action="store_const", const=None)
    parser.add_argument("--top-layer-multiplier", dest="top_layer_multiplier", type=float, default=.1)
    parser.add_argument("--all-layer-multiplier", dest="all_layer_multiplier", type=float, default=.01)
    parser.add_argument("--best-model", "-bm", dest="best_model", type=str, 
                        default="./modelState/patch_clf.h5")
    parser.add_argument("--final-model", "-fm", dest="final_model", type=str, 
                        default="NOSAVE")

    args = parser.parse_args()
    run_opts = dict(
        img_size=args.img_size, 
        img_scale=args.img_scale, 
        featurewise_center=args.featurewise_center,
        featurewise_mean=args.featurewise_mean,
        equalize_hist=args.equalize_hist,
        batch_size=args.batch_size, 
        train_bs_multiplier=args.train_bs_multiplier,
        augmentation=args.augmentation,
        class_list=args.class_list,
        nb_epoch=args.nb_epoch, 
        top_layer_epochs=args.top_layer_epochs,
        all_layer_epochs=args.all_layer_epochs,
        load_val_ram=args.load_val_ram,
        load_train_ram=args.load_train_ram,
        net=args.net,
        nb_init_filter=args.nb_init_filter, 
        init_filter_size=args.init_filter_size, 
        init_conv_stride=args.init_conv_stride, 
        pool_size=args.pool_size, 
        pool_stride=args.pool_stride, 
        weight_decay=args.weight_decay,
        weight_decay2=args.weight_decay2,
        bias_multiplier=args.bias_multiplier,
        alpha=args.alpha,
        l1_ratio=args.l1_ratio,
        inp_dropout=args.inp_dropout,
        hidden_dropout=args.hidden_dropout,
        hidden_dropout2=args.hidden_dropout2,
        optim=args.optim,
        init_lr=args.init_lr,
        lr_patience=args.lr_patience, 
        es_patience=args.es_patience,
        resume_from=args.resume_from,
        auto_batch_balance=args.auto_batch_balance,
        pos_cls_weight=args.pos_cls_weight,
        neg_cls_weight=args.neg_cls_weight,
        use_pretrained=args.use_pretrained,
        top_layer_nb=args.top_layer_nb,
        top_layer_multiplier=args.top_layer_multiplier,
        all_layer_multiplier=args.all_layer_multiplier,
        best_model=args.best_model,        
        final_model=args.final_model        
    )
    print "\ntrain_dir=%s" % (args.train_dir)
    print "val_dir=%s" % (args.val_dir)
    print "test_dir=%s" % (args.test_dir)
    print "\n>>> Model training options: <<<\n", run_opts, "\n"
    run(args.train_dir, args.val_dir, args.test_dir, **run_opts)









