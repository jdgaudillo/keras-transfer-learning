from keras.preprocessing import image
from keras.layers.core import Lambda
from keras.models import Sequential,Model
from tensorflow.python.framework import ops
import keras.backend as K
import tensorflow as tf
import numpy as np
import keras
import cv2


def target_category_loss(x, category_index, nb_classes):
    return tf.multiply(x, K.one_hot([category_index], nb_classes))


def target_category_loss_output_shape(input_shape):
    return input_shape


def normalize(x):
    # utility function to normalize a tensor by its L2 norm
    return x / (K.sqrt(K.mean(K.square(x))) + 1e-5)


def load_image(path, color_mode='bgr'):
    img_path = path
    img = image.load_img(img_path, target_size=(224, 224), color_mode=color_mode)
    x = image.img_to_array(img)
    x = np.expand_dims(x, axis=0)
    def preprocess_input(x):
        x /= 255.
        x -= 0.5
        x *= 2.
        return x
    x = preprocess_input(x)
    return x


def register_gradient():
    if "GuidedBackProp" not in ops._gradient_registry._registry:
        @ops.RegisterGradient("GuidedBackProp")
        def _GuidedBackProp(op, grad):
            dtype = op.inputs[0].dtype
            return grad * tf.cast(grad > 0., dtype) * \
                   tf.cast(op.inputs[0] > 0., dtype)


def compile_saliency_function(model, activation_layer='block14_sepconv2_act'):
    input_img = model.input
    layer_dict = dict([(layer.name, layer) for layer in model.layers[1:]])
    layer_output = layer_dict[activation_layer].output
    max_output = K.max(layer_output, axis=3)
    saliency = K.gradients(K.sum(max_output), input_img)[0]
    return K.function([input_img, K.learning_phase()], [saliency])


def modify_backprop(model, name):
    g = tf.get_default_graph()
    with g.gradient_override_map({'Relu': name}):

        # get layers that have an activation
        layer_dict = [layer for layer in model.layers[1:]
                      if hasattr(layer, 'activation')]

        # replace relu activation
        for layer in layer_dict:
            if layer.activation == keras.activations.relu:
                layer.activation = tf.nn.relu

        # re-instanciate a new model
        model_module = util.get_model_class_instance()
        model = model_module.load()
        new_model = model
    return new_model


def deprocess_image(x):
    '''
    Same normalization as in:
    https://github.com/fchollet/keras/blob/master/examples/conv_filter_visualization.py
    '''
    if np.ndim(x) > 3:
        x = np.squeeze(x)
    # normalize tensor: center on 0., ensure std is 0.1
    x -= x.mean()
    x /= (x.std() + 1e-5)
    x *= 0.1

    # clip to [0, 1]
    x += 0.5
    x = np.clip(x, 0, 1)

    # convert to RGB array
    x *= 255
    if K.image_dim_ordering() == 'th':
        x = x.transpose((1, 2, 0))
    x = np.clip(x, 0, 255).astype('uint8')
    return x


def _compute_gradients(tensor, var_list):
    grads = tf.gradients(tensor, var_list)
    return [grad if grad is not None else tf.zeros_like(var)
            for var, grad in zip(var_list, grads)]


def grad_cam(input_model, image, category_index, layer_name):
    nb_classes = 360
    '''
    I want to see the global_avg_pooling and the output vector directly but not the category_index one
    because our model is not connected the conv to the softmax
    -7 is the global_avg_pooling layer
    '''
    target_layer = input_model.layers[-7].output#lambda x: target_category_loss(x, category_index, nb_classes)
    #x = Lambda(target_layer, output_shape = target_category_loss_output_shape)(input_model.layers[132].output) #input_model.output[0]
    model = Model(inputs=input_model.input, outputs=target_layer)
    loss = K.sum(model.layers[-1].output)
    # conv_output = [l for l in model.layers[0].layers if l.name is layer_name][0].output
    conv_output = [l for l in model.layers if l.name is layer_name][0].output
    grads = normalize(K.gradients(loss, conv_output)[0]) # normalize(_compute_gradients(loss, [conv_output])[0])
    gradient_function = K.function([model.layers[0].input], [conv_output, grads])

    #7*7*2048 shape output and 2048 shape grads_val
    output, grads_val = gradient_function([image])
    output, grads_val = output[0, :], grads_val[0, :, :, :]

    weights = np.mean(grads_val, axis=(0, 1))
    cam = np.ones(output.shape[0: 2], dtype=np.float32)

    for i, w in enumerate(weights):
        cam += w * output[:, :, i]
    # here we got the 7*7 filter for xception mattered with the 2048 output vector for -7 layer
    cam = cv2.resize(cam, (224, 224))
    cam = np.maximum(cam, 0)
    heatmap = cam / np.max(cam)

    # Return to BGR [0..255] from the preprocessed image
    image = image[0, :]
    image = image*127.5 + 127.5
    image = np.array(image, dtype=np.float32)
    # image -= np.min(image)
    # image = np.minimum(image, 255)

    cam = cv2.applyColorMap(np.uint8(255 * heatmap), cv2.COLORMAP_JET)
    cam = np.float32(cam) + np.float32(image)
    cam = 255 * cam / np.max(cam)
    return np.uint8(cam), heatmap

preprocessed_input = load_image(r'C:/Users/Ctbri/Desktop/110087_2_2_151819165.jpg', color_mode='grayscale')
preprocessed_input = preprocessed_input[:, ::-1, :, :]
import util
import config
config.model = 'xception'
util.set_img_format()
model_module = util.get_model_class_instance()
model = model_module.load()
classes_in_keras_format = util.get_classes_in_keras_format()

predictions = model.predict(preprocessed_input)[0] #predictions = model.predict([preprocessed_input,np.asarray([[1]])])[0]

predicted_class = np.argmax(predictions)
print(list(classes_in_keras_format.keys())[list(classes_in_keras_format.values()).index(predicted_class)])
cam, heatmap = grad_cam(model, preprocessed_input, predicted_class, "block14_sepconv2_act")
cv2.imshow("gradcam.jpg", cam)
cv2.waitKey(1000)

register_gradient()
guided_model = modify_backprop(model, 'GuidedBackProp')
saliency_fn = compile_saliency_function(guided_model)
saliency = saliency_fn([preprocessed_input, 0])
gradcam = saliency[0] * heatmap[..., np.newaxis]
cv2.imshow("guided_gradcam.jpg", deprocess_image(gradcam))
cv2.waitKey(1000)