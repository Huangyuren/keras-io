"""
Title: Tune hyperparameters in your custom training loop
Authors: Tom O'Malley, Haifeng Jin
Date created: 2019/10/28
Last modified: 2022/01/12
Description: Use `HyperModel.fit()` to tune training hyperparameters (such as batch size).
"""

"""shell
pip install keras-tuner -q
"""

"""
## Introduction

The `HyperModel` class in KerasTuner provides a convenient way to
define your search space in a reusable object.
You can override `HyperModel.build()` to define and hypertune the
model itself. To hypertune the training process
(e.g. by selecting the proper batch size, number of training epochs, or data augmentation setup),
you can override `HyperModel.fit()`, where you can access:

- The `hp` object, which is an instance of `kt.HyperParameters`
- The model built by `HyperModel.build()`

A basic example is shown in the "tune model training" section of
[Getting Started with KerasTuner](https://keras.io/guides/keras_tuner/getting_started/#tune-model-training).

## Tuning the custom training loop

In this guide, we will subclass the `HyperModel` class and write a custom
training loop by overriding `HyperModel.fit()`. For how to write a custom
training loop with Keras, you can refer to the guide
[Writing a training loop from scratch](https://keras.io/guides/writing_a_training_loop_from_scratch/).

First, we import the libraries we need, and we create datasets for training and
validation. Here, we just use some random data for demonstration purposes.
"""

import keras_tuner as kt
import tensorflow as tf
from tensorflow import keras
import numpy as np


x_train = np.random.rand(1000, 28, 28, 1)
y_train = np.random.randint(0, 10, (1000, 1))
x_val = np.random.rand(1000, 28, 28, 1)
y_val = np.random.randint(0, 10, (1000, 1))

"""
Then, we subclass the `HyperModel` class as `MyHyperModel`. In
`MyHyperModel.build()`, we build a simple Keras model to do image
classification for 10 different classes. `MyHyperModel.fit()` accepts several
arguments. Its signature is shown below:

```python
def fit(self, hp, model, x, y, validation_data, callbacks=None, **kwargs):
```

* The `hp` argument is for defining the hyperparameters.
* The `model` argument is the model returned by `MyHyperModel.build()`.
* `x`, `y`, and `validation_data` are all custom-defined arguments. We will
pass our data to them by calling `tuner.search(x=x, y=y,
validation_data=(x_val, y_val))` later. You can define any number of them and
give custom names.
* The `callbacks` argument was intended to be used with `model.fit()`.
KerasTuner put some helpful Keras callbacks in it, for example, the callback
for checkpointing the model at its best epoch.

We will manually call the callbacks in the custom training loop. Before we
can call them, we need to assign our model to them with the following code so
that they have access to the model for checkpointing.

```py
for callback in callbacks:
    callback.model = model
```

In this example, we only called the `on_epoch_end()` method of the callbacks
to help us checkpoint the model. You may also call other callback methods
if needed. If you don't need to save the model, you don't need to use the
callbacks.

In the custom training loop, we tune the batch size of the dataset as we wrap
the NumPy data into a `tf.data.Dataset`. Note that you can tune any
preprocessing steps here as well. We also tune the learning rate of the
optimizer.

We will use the validation loss as the evaluation metric for the model. To
compute the mean validation loss, we will use `keras.metrics.Mean()`, which
averages the validation loss across the batches. We need to return the
validation loss for the tuner to make a record.
"""


class MyHyperModel(kt.HyperModel):
    def build(self, hp):
        """Builds a convolutional model."""
        inputs = keras.Input(shape=(28, 28, 1))
        x = keras.layers.Flatten()(inputs)
        x = keras.layers.Dense(
            units=hp.Choice("units", [32, 64, 128]), activation="relu"
        )(x)
        outputs = keras.layers.Dense(10)(x)
        return keras.Model(inputs=inputs, outputs=outputs)

    def fit(self, hp, model, x, y, validation_data, callbacks=None, **kwargs):
        # Convert the datasets to tf.data.Dataset.
        batch_size = hp.Int("batch_size", 32, 128, step=32, default=64)
        train_ds = tf.data.Dataset.from_tensor_slices((x_train, y_train)).batch(
            batch_size
        )
        validation_data = tf.data.Dataset.from_tensor_slices(validation_data).batch(
            batch_size
        )

        # Define the optimizer.
        optimizer = keras.optimizers.Adam(
            hp.Float("learning_rate", 1e-4, 1e-2, sampling="log", default=1e-3)
        )
        loss_fn = keras.losses.SparseCategoricalCrossentropy(from_logits=True)

        # The metric to track validation loss.
        epoch_loss_metric = keras.metrics.Mean()

        # Function to run the train step.
        @tf.function
        def run_train_step(images, labels):
            with tf.GradientTape() as tape:
                logits = model(images)
                loss = loss_fn(labels, logits)
                # Add any regularization losses.
                if model.losses:
                    loss += tf.math.add_n(model.losses)
            gradients = tape.gradient(loss, model.trainable_variables)
            optimizer.apply_gradients(zip(gradients, model.trainable_variables))

        # Function to run the validation step.
        @tf.function
        def run_val_step(images, labels):
            logits = model(images)
            loss = loss_fn(labels, logits)
            # Update the metric.
            epoch_loss_metric.update_state(loss)

        # Assign the model to the callbacks.
        for callback in callbacks:
            callback.model = model

        # Record the best validation loss value
        best_epoch_loss = float("inf")

        # The custom training loop.
        for epoch in range(2):
            print(f"Epoch: {epoch}")

            # Iterate the training data to run the training step.
            for images, labels in train_ds:
                run_train_step(images, labels)

            # Iterate the validation data to run the validation step.
            for images, labels in validation_data:
                run_val_step(images, labels)

            # Calling the callbacks after epoch.
            epoch_loss = float(epoch_loss_metric.result().numpy())
            for callback in callbacks:
                # The "my_metric" is the objective passed to the tuner.
                callback.on_epoch_end(epoch, logs={"my_metric": epoch_loss})
            epoch_loss_metric.reset_states()

            print(f"Epoch loss: {epoch_loss}")
            best_epoch_loss = min(best_epoch_loss, epoch_loss)

        # Return the evaluation metric value.
        return best_epoch_loss


"""
Now, we can initialize the tuner. Here, we use `Objective("my_metric", "min")`
as our metric to be minimized. The objective name should be consistent
with the one you use as the key in the
`logs` passed to the 'on_epoch_end()' method of the callbacks.
The callbacks need to use
this value in the `logs` to find the best epoch to checkpoint the model.

"""
tuner = kt.RandomSearch(
    objective=kt.Objective("my_metric", "min"),
    max_trials=2,
    hypermodel=MyHyperModel(),
    directory="results",
    project_name="custom_training",
    overwrite=True,
)


"""
We start the search by passing the arguments we defined in the signature of
`MyHyperModel.fit()` to `tuner.search()`.
"""

tuner.search(x=x_train, y=y_train, validation_data=(x_val, y_val))

"""
Finally, we can retrieve the results.
"""

best_hps = tuner.get_best_hyperparameters()[0]
print(best_hps.values)

best_model = tuner.get_best_models()[0]
best_model.summary()

"""
In summary, to tune the hyperparameters in your custom training loop, you just
override `HyperModel.fit()` to train the model and return the evaluation
results. With the provided callbacks, you can easily save the trained models at
their best epochs and load the best models later.

To find out more about the basics of KerasTuner, please see
[Getting Started with KerasTuner](https://keras.io/guides/keras_tuner/getting_started/).
"""
