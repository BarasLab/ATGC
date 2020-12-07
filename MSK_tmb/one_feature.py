import numpy as np
import tensorflow as tf
from model.Sample_MIL import InstanceModels, RaggedModels, SampleModels
from model import DatasetsUtils
from sklearn.model_selection import StratifiedShuffleSplit, StratifiedKFold
import pickle
physical_devices = tf.config.experimental.list_physical_devices('GPU')
tf.config.experimental.set_memory_growth(physical_devices[3], True)
tf.config.experimental.set_visible_devices(physical_devices[3], 'GPU')
import pathlib

path = pathlib.Path.cwd()
if path.stem == 'ATGC2':
    cwd = path
else:
    cwd = list(path.parents)[::-1][path.parts.index('ATGC2')]
    import sys
    sys.path.append(str(cwd))


D, samples, maf, sample_df = pickle.load(open(pathlib.Path('/home/janaya2/Desktop/ATGC2') / 'MSK_tmb' / 'data' / 'all_cancers_data.pkl', 'rb'))
panels = pickle.load(open(pathlib.Path('/home/janaya2/Desktop/ATGC2') / '..' / 'ATGC_paper' / 'files' / 'tcga_panel_table.pkl', 'rb'))

strand_emb_mat = np.concatenate([np.zeros(2)[np.newaxis, :], np.diag(np.ones(2))], axis=0)
D['strand_emb'] = strand_emb_mat[D['strand']]

chr_emb_mat = np.concatenate([np.zeros(24)[np.newaxis, :], np.diag(np.ones(24))], axis=0)
D['chr_emb'] = chr_emb_mat[D['chr']]

frame_emb_mat = np.concatenate([np.zeros(3)[np.newaxis, :], np.diag(np.ones(3))], axis=0)
D['cds_emb'] = frame_emb_mat[D['cds']]

indexes = [np.where(D['sample_idx'] == idx) for idx in range(len(samples['histology']))]

five_p = np.array([D['seq_5p'][i] for i in indexes], dtype='object')
three_p = np.array([D['seq_3p'][i] for i in indexes], dtype='object')
ref = np.array([D['seq_ref'][i] for i in indexes], dtype='object')
alt = np.array([D['seq_alt'][i] for i in indexes], dtype='object')
strand = np.array([D['strand_emb'][i] for i in indexes], dtype='object')

##bin position
def pos_one_hot(pos):
    one_pos = int(pos * 100)
    return one_pos, (pos * 100) - one_pos

result = np.apply_along_axis(pos_one_hot, -1, D['pos_float'][:, np.newaxis])

D['pos_bin'] = np.stack(result[:, 0]) + 1
D['pos_loc'] = np.stack(result[:, 1])
ones = np.array([np.ones_like(D['pos_loc'][i]) for i in indexes], dtype='object')

# set y label
y_label = np.log(sample_df['non_syn_counts'].values / (panels.loc[panels['Panel'] == 'Agilent_kit']['cds'].values[0]/1e6) + 1)[:, np.newaxis]
y_strat = np.argmax(samples['histology'], axis=-1)

runs = 1
initial_weights = []
metrics = [RaggedModels.losses.QuantileLoss()]
losses = [RaggedModels.losses.QuantileLoss()]

for i in range(runs):
    tile_encoder = InstanceModels.PassThrough(shape=(1, ))
    sample_encoder = SampleModels.PassThrough(shape=(samples['histology'].shape[-1], ))
    mil = RaggedModels.MIL(mode='aggregation', instance_encoders=[tile_encoder.model], pooled_layers=[64], sample_layers=[64, 32], sample_encoders=[sample_encoder.model], output_dim=1, output_type='quantiles')
    # mil.model.trainable = False
    mil.model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=0.001), loss=losses, metrics=metrics)
    initial_weights.append(mil.model.get_weights())

##stratified K fold for test
for idx_train, idx_test in StratifiedKFold(n_splits=8, random_state=0, shuffle=True).split(y_strat, y_strat):
    pass

idx_train, idx_valid = [idx_train[idx] for idx in list(StratifiedShuffleSplit(n_splits=1, test_size=1500, random_state=0).split(np.zeros_like(y_strat)[idx_train], y_strat[idx_train]))[0]]


from sklearn.model_selection import RepeatedStratifiedKFold

class Apply:
    class StratifiedMinibatch:
        def __init__(self, batch_size, ds_size):
            self.batch_size, self.ds_size = batch_size, ds_size
            # max number of splits
            self.n_splits = self.ds_size // self.batch_size
            # stratified "mini-batch" via k-fold
            self.batcher = RepeatedStratifiedKFold(n_splits=self.n_splits, n_repeats=10000)

        def __call__(self, ds_input: tf.data.Dataset):
            def generator():
                idx, *samples, y_true, y_strat = list(map(tf.stack, list(map(list, zip(*list(ds_input.take(self.n_splits * self.batch_size)))))))
                for _, idx_batch in self.batcher.split(y_strat, y_strat):
                    samples_batched = [tf.gather(sample, idx_batch, axis=0) for sample in samples]
                    yield(tuple([tf.gather(idx, idx_batch, axis=0)] + samples_batched + [tf.gather(y_true, idx_batch, axis=0)]))

            return tf.data.Dataset.from_generator(generator,
                                                  output_types=tuple([i.dtype for i in ds_input.element_spec[: -1]]),
                                                  )




ds_train = tf.data.Dataset.from_tensor_slices((idx_train, samples['histology'][idx_train], y_label[idx_train], y_strat[idx_train]))
# ds_train.shuffle(buffer_size=len(idx_train), reshuffle_each_iteration=True)
ds_train = ds_train.apply(Apply.StratifiedMinibatch(batch_size=512, ds_size=len(idx_train)))
x_loader = DatasetsUtils.Map.LoadBatchIndex(loaders=[DatasetsUtils.Loaders.FromNumpy(ones, tf.float32)])
ds_train = ds_train.repeat().map(lambda *args: (((x_loader(args[0], to_ragged=[True]), ) + args[1: -1]), args[-1]))



ds_valid = tf.data.Dataset.from_tensor_slices((idx_valid, samples['histology'][idx_valid], y_label[idx_valid], y_strat[idx_valid]))
# ds_valid = ds_valid.apply(Apply.StratifiedMinibatch(batch_size=512, ds_size=len(idx_valid)))
ds_valid = ds_valid.batch(len(idx_valid), drop_remainder=False)
ds_valid = ds_valid.map(lambda *args: (((x_loader(args[0], to_ragged=[True]), ) + args[1: -1]), args[-1]))



callbacks = [tf.keras.callbacks.EarlyStopping(monitor='val_quantile_loss', min_delta=0.0001, patience=40, mode='min', restore_best_weights=True)]

for initial_weight in initial_weights:
    mil.model.set_weights(initial_weight)
    mil.model.fit(ds_train,
                  steps_per_epoch=20,
                  # validation_data=ds_valid,
                  epochs=1000,
                  # callbacks=callbacks
                  )





#
# ds_all = tf.data.Dataset.from_tensor_slices((np.arange(len(y_label)), samples['histology'], y_label))
# ds_all = ds_all.batch(len(y_label))
# ds_all = ds_all.map(lambda *args: (((x_loader(args[0], to_ragged=[True]), ) + args[1: -1]), args[-1]))

# predictions = mil.model.predict(ds_train)

#
# for cancer in np.unique(sample_df['type'].values):
#     mask = sample_df['type'] == cancer
#     print(cancer, round(np.mean((y_label[:,0][mask] - predictions[:, 1][mask])**2), 4), sum(mask))


# #mae
# print(round(np.mean(np.absolute(y_label[:, 0][np.concatenate(test_idx)] - np.concatenate(predictions)[:, 1])), 4))
# #r2
# print(round(r2_score(np.concatenate(predictions)[:, 1], y_label[:, 0][np.concatenate(test_idx)]), 4))

