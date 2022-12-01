import os
import urllib
import traceback
import time
import sys
import numpy as np
import cv2
from rknn.api import RKNN

ONNX_MODEL = 'yolov7-tiny.onnx'
RKNN_MODEL = 'yolov7-tiny.rknn'
IMG_PATH = './bus.jpg'
DATASET = './dataset.txt'

QUANTIZE_ON = True

BOX_THESH = 0.45
NMS_THRESH = 0.25
IMG_SIZE = 640

CLASSES = ("person", "bicycle", "car", "motorbike ", "aeroplane ", "bus ", "train", "truck ", "boat", "traffic light",
           "fire hydrant", "stop sign ", "parking meter", "bench", "bird", "cat", "dog ", "horse ", "sheep", "cow",
           "elephant",
           "bear", "zebra ", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis",
           "snowboard", "sports ball", "kite",
           "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
           "fork", "knife ",
           "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza ", "donut",
           "cake", "chair", "sofa",
           "pottedplant", "bed", "diningtable", "toilet ", "tvmonitor", "laptop	", "mouse	", "remote ",
           "keyboard ", "cell phone", "microwave ",
           "oven ", "toaster", "sink", "refrigerator ", "book", "clock", "vase", "scissors ", "teddy bear ",
           "hair drier", "toothbrush ")


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def xywh2xyxy(x):
    # Convert [x, y, w, h] to [x1, y1, x2, y2]
    y = np.copy(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2  # top left x
    y[:, 1] = x[:, 1] - x[:, 3] / 2  # top left y
    y[:, 2] = x[:, 0] + x[:, 2] / 2  # bottom right x
    y[:, 3] = x[:, 1] + x[:, 3] / 2  # bottom right y
    return y


def process(input, mask, anchors):
    anchors = [anchors[i] for i in mask]
    grid_h, grid_w = map(int, input.shape[0:2])

    box_confidence = sigmoid(input[..., 4])
    box_confidence = np.expand_dims(box_confidence, axis=-1)

    box_class_probs = sigmoid(input[..., 5:])

    box_xy = sigmoid(input[..., :2]) * 2 - 0.5

    col = np.tile(np.arange(0, grid_w), grid_w).reshape(-1, grid_w)
    row = np.tile(np.arange(0, grid_h).reshape(-1, 1), grid_h)
    col = col.reshape(grid_h, grid_w, 1, 1).repeat(3, axis=-2)
    row = row.reshape(grid_h, grid_w, 1, 1).repeat(3, axis=-2)
    grid = np.concatenate((col, row), axis=-1)
    box_xy += grid
    box_xy *= int(IMG_SIZE / grid_h)

    box_wh = pow(sigmoid(input[..., 2:4]) * 2, 2)
    box_wh = box_wh * anchors

    box = np.concatenate((box_xy, box_wh), axis=-1)

    return box, box_confidence, box_class_probs


def filter_boxes(boxes, box_confidences, box_class_probs):
    """Filter boxes with box threshold. It's a bit different with origin yolov5 post process!

    # Arguments
        boxes: ndarray, boxes of objects.
        box_confidences: ndarray, confidences of objects.
        box_class_probs: ndarray, class_probs of objects.

    # Returns
        boxes: ndarray, filtered boxes.
        classes: ndarray, classes for boxes.
        scores: ndarray, scores for boxes.
    """
    box_classes = np.argmax(box_class_probs, axis=-1)
    box_class_scores = np.max(box_class_probs, axis=-1)
    pos = np.where(box_confidences[..., 0] >= BOX_THESH)

    boxes = boxes[pos]
    classes = box_classes[pos]
    scores = box_class_scores[pos]

    return boxes, classes, scores


def nms_boxes(boxes, scores):
    """Suppress non-maximal boxes.

    # Arguments
        boxes: ndarray, boxes of objects.
        scores: ndarray, scores of objects.

    # Returns
        keep: ndarray, index of effective boxes.
    """
    x = boxes[:, 0]
    y = boxes[:, 1]
    w = boxes[:, 2] - boxes[:, 0]
    h = boxes[:, 3] - boxes[:, 1]

    areas = w * h
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)

        xx1 = np.maximum(x[i], x[order[1:]])
        yy1 = np.maximum(y[i], y[order[1:]])
        xx2 = np.minimum(x[i] + w[i], x[order[1:]] + w[order[1:]])
        yy2 = np.minimum(y[i] + h[i], y[order[1:]] + h[order[1:]])

        w1 = np.maximum(0.0, xx2 - xx1 + 0.00001)
        h1 = np.maximum(0.0, yy2 - yy1 + 0.00001)
        inter = w1 * h1

        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(ovr <= NMS_THRESH)[0]
        order = order[inds + 1]
    keep = np.array(keep)
    return keep


def yolov5_post_process(input_data):
    masks = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    yolov5_anchors = [[10, 13], [16, 30], [33, 23],
                      [30, 61], [62, 45], [59, 119],
                      [116, 90], [156, 198], [373, 326]]

    yolov7_anchors = [[12, 16], [19, 36], [40, 28],
                      [36, 75], [75, 55], [72, 146],
                      [142, 110], [192, 243], [459, 401]]

    boxes, classes, scores = [], [], []
    for input, mask in zip(input_data, masks):
        b, c, s = process(input, mask, yolov5_anchors)
        b, c, s = filter_boxes(b, c, s)
        boxes.append(b)
        classes.append(c)
        scores.append(s)

    boxes = np.concatenate(boxes)
    boxes = xywh2xyxy(boxes)
    classes = np.concatenate(classes)
    scores = np.concatenate(scores)

    nboxes, nclasses, nscores = [], [], []
    for c in set(classes):
        inds = np.where(classes == c)
        b = boxes[inds]
        c = classes[inds]
        s = scores[inds]

        keep = nms_boxes(b, s)

        nboxes.append(b[keep])
        nclasses.append(c[keep])
        nscores.append(s[keep])

    if not nclasses and not nscores:
        return None, None, None

    boxes = np.concatenate(nboxes)
    classes = np.concatenate(nclasses)
    scores = np.concatenate(nscores)

    return boxes, classes, scores


def draw(image, boxes, scores, classes):
    """Draw the boxes on the image.

    # Argument:
        image: original image.
        boxes: ndarray, boxes of objects.
        classes: ndarray, classes of objects.
        scores: ndarray, scores of objects.
        all_classes: all classes name.
    """
    for box, score, cl in zip(boxes, scores, classes):
        top, left, right, bottom = box
        print('class: {}, score: {}'.format(CLASSES[cl], score))
        print('box coordinate left,top,right,down: [{}, {}, {}, {}]'.format(top, left, right, bottom))
        top = int(top)
        left = int(left)
        right = int(right)
        bottom = int(bottom)

        cv2.rectangle(image, (top, left), (right, bottom), (255, 0, 0), 2)
        cv2.putText(image, '{0} {1:.2f}'.format(CLASSES[cl], score),
                    (top, left + 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 0, 255), 2)


def letterbox(im, new_shape=(640, 640), color=(0, 0, 0)):
    # Resize and pad image while meeting stride-multiple constraints
    shape = im.shape[:2]  # current shape [height, width]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    # Scale ratio (new / old)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])

    # Compute padding
    ratio = r, r  # width, height ratios
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding

    dw /= 2  # divide padding into 2 sides
    dh /= 2

    if shape[::-1] != new_unpad:  # resize
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)  # add border
    return im, ratio, (dw, dh)

def get_rknn(model_path,device_id_list=None,rknn2precompile=False):
    rknn = RKNN()

    # load RKNN model
    print('--> Load RKNN model: {}'.format(model_path))
    ret = rknn.load_rknn(path=model_path)
    if ret != 0:
        print("load RKNN model: {} failed.".format(model_path))
        exit(-1)
    print('--> done')

    # init RKNN Runtime
    print('--> Init RKNN Runtime.')
    devices = rknn.list_devices()
    print("--> devices id {}".format(devices))
    if len(devices[0]) > 0:
        target = devices[0][0]
    else:
        target = ""
    if len(devices[1]) > 0:
        device_id = devices[1][0]
    else:
        device_id = ""
    print("--> device target = {},device_id = {}".format(target, device_id))
    if device_id:
        print(device_id_list,devices[1])
        if device_id_list:
            for device_id_tmp in devices[1]:
                if device_id_tmp not in device_id_list:
                    device_id = device_id_tmp
                    break

        if target:
            print("--> init target = {},device_id = {}".format(target, device_id))
            ret = rknn.init_runtime(target=target,device_id=device_id,rknn2precompile=rknn2precompile)
        else:
            print("--> init target = {},device_id = {}".format(target, device_id))
            ret = rknn.init_runtime(device_id=device_id,rknn2precompile=rknn2precompile)

    else:
        ret = rknn.init_runtime()
    if ret != 0:
        print("Init RKNN Runtime failed.")
        exit(-1)
    print('--> done')
    if device_id_list:
        device_id_list.append(device_id)

    return rknn,device_id_list


def get_rknn(model_path,device_id_list=None,rknn2precompile=False):
    rknn = RKNN()

    # load RKNN model
    print('--> Load RKNN model: {}'.format(model_path))
    ret = rknn.load_rknn(path=model_path)
    if ret != 0:
        print("load RKNN model: {} failed.".format(model_path))
        exit(-1)
    print('--> done')

    # init RKNN Runtime
    print('--> Init RKNN Runtime.')
    devices = rknn.list_devices()
    print("--> devices id {}".format(devices))
    if len(devices[0]) > 0:
        target = devices[0][0]
    else:
        target = ""
    if len(devices[1]) > 0:
        device_id = devices[1][0]
    else:
        device_id = ""
    print("--> device target = {},device_id = {}".format(target, device_id))
    if device_id:
        print(device_id_list,devices[1])
        if device_id_list:
            for device_id_tmp in devices[1]:
                if device_id_tmp not in device_id_list:
                    device_id = device_id_tmp
                    break

        if target:
            print("--> init target = {},device_id = {}".format(target, device_id))
            ret = rknn.init_runtime(target=target,device_id=device_id,rknn2precompile=rknn2precompile)
        else:
            print("--> init target = {},device_id = {}".format(target, device_id))
            ret = rknn.init_runtime(device_id=device_id,rknn2precompile=rknn2precompile)

    else:
        ret = rknn.init_runtime()
    if ret != 0:
        print("Init RKNN Runtime failed.")
        exit(-1)
    print('--> done')
    if device_id_list:
        device_id_list.append(device_id)

    return rknn,device_id_list


if __name__ == '__main__':
    rknn,_ = get_rknn(model_path=RKNN_MODEL)
    # Set inputs
    img = cv2.imread(IMG_PATH)
    # img, ratio, (dw, dh) = letterbox(img, new_shape=(IMG_SIZE, IMG_SIZE))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (IMG_SIZE, IMG_SIZE))

    # Inference
    print('--> Running model')
    outputs = rknn.inference(inputs=[img])
    # np.save('./onnx_yolov5_0.npy', outputs[0])
    # np.save('./onnx_yolov5_1.npy', outputs[1])
    # np.save('./onnx_yolov5_2.npy', outputs[2])
    print('done')

    # post process
    input0_data = outputs[0]
    input1_data = outputs[1]
    input2_data = outputs[2]

    input0_data = input0_data.reshape([3, -1] + list(input0_data.shape[-2:]))
    input1_data = input1_data.reshape([3, -1] + list(input1_data.shape[-2:]))
    input2_data = input2_data.reshape([3, -1] + list(input2_data.shape[-2:]))

    input_data = list()
    input_data.append(np.transpose(input0_data, (2, 3, 0, 1)))
    input_data.append(np.transpose(input1_data, (2, 3, 0, 1)))
    input_data.append(np.transpose(input2_data, (2, 3, 0, 1)))

    boxes, classes, scores = yolov5_post_process(input_data)

    img_1 = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    if boxes is not None:
        draw(img_1, boxes, scores, classes)
    # show output
    cv2.imwrite("result.jpg",img_1)
    cv2.imshow("post process result", img_1)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    rknn.release()
