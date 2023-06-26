import numpy as np
from model.Sample_MIL import RaggedModels, InstanceModels
from model.KerasLayers import Losses, Metrics
import tensorflow as tf
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedShuffleSplit, StratifiedKFold
from model import DatasetsUtils
import pickle
import pathlib
path = pathlib.Path.cwd()
if path.stem == 'ATGC':
    cwd = path
else:
    cwd = list(path.parents)[::-1][path.parts.index('ATGC')]
    import sys
    sys.path.append(str(cwd))

physical_devices = tf.config.experimental.list_physical_devices('GPU')
tf.config.experimental.set_memory_growth(physical_devices[-1], True)
tf.config.experimental.set_visible_devices(physical_devices[-1], 'GPU')

D, tcga_maf, samples = pickle.load(open(cwd / 'figures' / 'tumor_classification' / 'data' / 'data.pkl', 'rb'))
tcga_maf['Hugo_Symbol'] = tcga_maf['Hugo_Symbol'].astype('category')
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'PCPG' if x == 'Paraganglioma' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'PCPG' if x == 'Pheochromocytoma' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'SARC' if x == 'Desmoid-Type Fibromatosis' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'SARC' if x == 'Leiomyosarcoma' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'SARC' if x == 'Liposarcoma' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'SARC' if x == 'Malignant Peripheral Nerve Sheath Tumor' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'SARC' if x == 'Myxofibrosarcoma' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'SARC' if x == 'Synovial Sarcoma' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'SARC' if x == 'Undifferentiated Pleomorphic Sarcoma' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'TGCT' if x == 'Testicular Non-Seminomatous Germ Cell Tumor' else x)
samples['NCIt_label'] = samples['NCIt_label'].apply(lambda x: 'TGCT' if x == 'Testicular Seminoma' else x)

labels_to_use = ['Muscle-Invasive Bladder Carcinoma', 'Infiltrating Ductal Breast Carcinoma',
                 'Invasive Lobular Breast Carcinoma', 'Cervical Squamous Cell Carcinoma',
                 'Colorectal Adenocarcinoma', 'Glioblastoma', 'Head and Neck Squamous Cell Carcinoma',
                 'Clear Cell Renal Cell Carcinoma', 'Papillary Renal Cell Carcinoma',
                 'Astrocytoma', 'Oligoastrocytoma', 'Oligodendroglioma', 'Hepatocellular Carcinoma',
                 'Lung Adenocarcinoma', 'Lung Squamous Cell Carcinoma', 'Ovarian Serous Adenocarcinoma',
                 'Adenocarcinoma, Pancreas', 'PCPG', 'Prostate Acinar Adenocarcinoma',
                 'SARC', 'Cutaneous Melanoma', 'Gastric Adenocarcinoma',
                 'TGCT', 'Thyroid Gland Follicular Carcinoma', 'Thyroid Gland Papillary Carcinoma',
                 'Endometrial Endometrioid Adenocarcinoma', 'Endometrial Serous Adenocarcinoma']

samples = samples.loc[samples['NCIt_label'].isin(labels_to_use)]

D['genes'] = np.concatenate(tcga_maf[['Hugo_Symbol']].apply(lambda x: x.cat.codes).values + 1)

input_dim = max(D['genes'])
dropout = 0
indexes = [np.where(D['sample_idx'] == idx) for idx in samples.index]
genes = np.array([D['genes'][i] for i in indexes], dtype='object')
index_loader = DatasetsUtils.Map.FromNumpytoIndices([j for i in indexes for j in i], dropout=dropout)
genes_loader = DatasetsUtils.Map.FromNumpyandIndices(genes, tf.int16)
genes_loader_eval = DatasetsUtils.Map.FromNumpy(genes, tf.int16, dropout=0)

A = samples['NCIt_label'].astype('category')
classes = A.cat.categories.values
classes_onehot = np.eye(len(classes))[A.cat.codes]
y_label = classes_onehot

y_strat = np.argmax(y_label, axis=-1)
class_counts = dict(zip(*np.unique(y_strat, return_counts=True)))
y_weights = np.array([1 / class_counts[_] for _ in y_strat])
y_weights /= np.sum(y_weights)

y_label_loader = DatasetsUtils.Map.FromNumpy(y_label, tf.float32)
y_weights_loader = DatasetsUtils.Map.FromNumpy(y_weights, tf.float32)

predictions = []
test_idx = []
weights = []
aucs = []
callbacks = [tf.keras.callbacks.EarlyStopping(monitor='val_weighted_CE', min_delta=0.001, patience=30, mode='min', restore_best_weights=True)]
for idx_train, idx_test in StratifiedKFold(n_splits=5, random_state=0, shuffle=True).split(y_strat, y_strat):
    eval=100
    test_idx.append(idx_test)
    idx_train, idx_valid = [idx_train[idx] for idx in list(StratifiedShuffleSplit(n_splits=1, test_size=1000, random_state=0).split(np.zeros_like(y_strat)[idx_train], y_strat[idx_train]))[0]]
    with tf.device('/cpu:0'):
        ds_train = tf.data.Dataset.from_tensor_slices((idx_train, y_strat[idx_train]))
        ds_train = ds_train.apply(DatasetsUtils.Apply.StratifiedMinibatch(batch_size=512, ds_size=len(idx_train)))

        ds_train = ds_train.map(lambda x: ((index_loader(x),)),)

        ds_train = ds_train.map(lambda x: ((genes_loader(x[0], x[1]),), (y_label_loader(x[0]),), y_weights_loader(x[0])),)

        ds_train.prefetch(1)
        ds_valid = tf.data.Dataset.from_tensor_slices(((
                                               genes_loader_eval(idx_valid),
                                           ),
                                            (
                                                tf.gather(y_label, idx_valid),
                                            ),
                                            tf.gather(y_weights, idx_valid)
                                            ))
        ds_valid = ds_valid.batch(len(idx_valid), drop_remainder=False)

    losses = [Losses.CrossEntropy()]

    for i in range(3):
        gene_encoder = InstanceModels.GeneEmbed(shape=(), input_dim=input_dim, dim=128)
        mil = RaggedModels.MIL(instance_encoders=[gene_encoder.model], sample_encoders=[], heads=y_label.shape[-1], output_dims=[y_label.shape[-1]], mil_hidden=[], attention_layers=[], instance_dropout=0, regularization=0, input_dropout=dropout, weight_decay=.0005)

        mil.model.compile(loss=losses,
                          metrics=[Metrics.CrossEntropy(), Metrics.Accuracy()],
                          weighted_metrics=[Metrics.CrossEntropy()],
                          optimizer=tf.keras.optimizers.Adam(learning_rate=0.001,
                                                             ))

        mil.model.fit(ds_train,
                      steps_per_epoch=20,
                      epochs=20000,
                      validation_data=ds_valid,
                      callbacks=callbacks,
                      )

        run_eval = mil.model.evaluate(ds_valid)[-1]

        if run_eval < eval:
            eval = run_eval
            run_weights = mil.model.get_weights()

    weights.append(run_weights)


with open(cwd / 'figures' / 'tumor_classification' / 'ncit' / 'mil_encoder' / 'results' / 'gene_weights.pkl', 'wb') as f:
    pickle.dump([test_idx, weights], f)


for index, (idx_train, idx_test) in enumerate(StratifiedKFold(n_splits=5, random_state=0, shuffle=True).split(y_strat, y_strat)):
    mil.model.set_weights(weights[index])
    ds_test = tf.data.Dataset.from_tensor_slices(((
                                               genes_loader_eval(idx_test),
                                           ),
                                            (
                                                tf.gather(y_label, idx_test),
                                            ),
                                            tf.gather(y_weights, idx_test)
                                            ))
    ds_test = ds_test.batch(50, drop_remainder=False)
    predictions.append(mil.model.predict(ds_test))


P = np.concatenate(predictions)
#convert the model logits to probabilities
z = np.exp(P - np.max(P, axis=1, keepdims=True))
predictions = z / np.sum(z, axis=1, keepdims=True)

with open(cwd / 'figures' / 'tumor_classification' / 'ncit' / 'mil_encoder' / 'results' / 'gene_predictions.pkl', 'wb') as f:
    pickle.dump([predictions, y_label, test_idx], f)

print(np.sum((np.argmax(predictions, axis=-1) == np.argmax(y_label[np.concatenate(test_idx)], axis=-1)) * y_weights[np.concatenate(test_idx)]))
print(sum(np.argmax(predictions, axis=-1) == np.argmax(y_label[np.concatenate(test_idx)], axis=-1)) / len(y_label))
print(roc_auc_score(np.argmax(y_label[np.concatenate(test_idx)], axis=-1), predictions, multi_class='ovr'))

# 0.547066287421338
# 0.5863075196408529
# 0.9404138308059325
