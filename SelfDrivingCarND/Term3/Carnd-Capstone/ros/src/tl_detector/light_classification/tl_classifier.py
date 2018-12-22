import rospy
from styx_msgs.msg import TrafficLight
import tensorflow as tf
from PIL import Image
import cv2
import numpy as np
from glob import glob
import os
from keras import backend as K
from keras.models import load_model
from keras.utils import plot_model
import time

class TLClassifier(object):
    def __init__(self, is_site):
        self.state = 0
        self.out = 0
        self.done= 0
        cwd = os.path.dirname(os.path.realpath(__file__))
        print "path" , cwd

        self.class_graph = tf.get_default_graph()

        # detection graph
        self.dg = tf.Graph()
        # classification graph 
        self.cl = tf.Graph()

        with self.cl.as_default():
            #open keras classification model
            sess= tf.Session()
            K.set_session(sess)
            model = 'carla_aug.h5' if is_site else 'model.h5'
            self.class_model =load_model(cwd+'/models/'+model)
            self.session_cl = tf.Session(graph=self.cl )

        with self.dg.as_default():
            gdef = tf.GraphDef()
            with open(cwd + "/models/frozen_inference_graph.pb", 'rb') as f:
                gdef.ParseFromString( f.read() )
                tf.import_graph_def( gdef, name="" )

            self.session_dg = tf.Session(graph=self.dg )
            self.image_tensor = self.dg.get_tensor_by_name('image_tensor:0')
            self.detection_boxes =  self.dg.get_tensor_by_name('detection_boxes:0')
            self.detection_scores = self.dg.get_tensor_by_name('detection_scores:0')
            self.detection_classes = self.dg.get_tensor_by_name('detection_classes:0')
            self.num_detections    = self.dg.get_tensor_by_name('num_detections:0')

        self.tlclasses = [ TrafficLight.RED, TrafficLight.YELLOW, TrafficLight.GREEN ]
        self.tlclasses_d = { TrafficLight.RED : "RED", TrafficLight.YELLOW:"YELLOW", TrafficLight.GREEN:"GREEN", TrafficLight.UNKNOWN:"UNKNOWN" }

    def get_classification(self, image, st):
        """Determines the color of the traffic light in the image

        Args:
            image (cv::Mat): image containing the traffic light. OpenCV is BGR by default.

        Returns:
            int: ID of traffic light color (specified in styx_msgs/TrafficLight)

        """
        box = self.localize_lights( image )
        if box is None:
            return TrafficLight.UNKNOWN
        class_image = cv2.resize( image[box[0]:box[2], box[1]:box[3]], (32,32) )
        if self.out:
            cv2.imwrite('/home/carkyo/imageration.jpg',class_image)
            self.done=1

        light_state =  self.classify_lights( class_image )
        #debug comments
        colors = ['red','yellow','green']
        rospy.loginfo('Detected light is classified as '+colors[light_state])
        return light_state



    def classify_lights_old(self, image):

        """ Given a 32x32x3 image classifies it as red, greed or yellow
            Expects images in BGR format. Important otherwide won't classify correctly
            
        
        status = TrafficLight.UNKNOWN
        img_resize = np.expand_dims(image, axis=0).astype('float32')
        with self.class_graph.as_default():
            predict = self.class_model.predict(img_resize)
            status  = self.tlclasses[ np.argmax(predict) ]

        return status
        """

        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image = image.reshape((image.shape[0] * image.shape[1], 3))
        r=0.0;g=0.0;y=0.0
        for i in range (len(image)):
            red=image[i][0]    
            green=image[i][1]
            blue=image[i][2]
            if ( (red > green+100) and (red > blue+100) and (red > 190) ):  
                r+=1         
            elif ( (green > red+100) and (green > blue+100) and (green > 190) ): 
                g+=1          
            elif ( (red > 200) and (green > 200) and (blue < 100) ):  
                y+=1         

        if (r > max(g,y) ):
            self.state=0
        elif (g > max(y,r) ):
            self.state=2
        elif (y > max(g,r) ):
            self.state=1


        rospy.loginfo (str(self.state))
        return self.state

    def classify_lights(self,image):
        with self.cl.as_default():
            #image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            #rospy.loginfo(type(image))
            #rospy.loginfo(str(image.shape))
            #rospy.loginfo(image.dtype)
            state = self.class_model.predict(np.array([image]))
            return np.argmax(state)

    def localize_lights(self, image):
        """ Localizes bounding boxes for lights using pretrained TF model
            expects BGR8 image
        """
        with self.dg.as_default():
            #switch from BGR to RGB. Important otherwise detection won't work
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

            tf_image_input = np.expand_dims(image,axis=0)
            #run detection model
            (detection_boxes, detection_scores, detection_classes, num_detections) = self.session_dg.run(
                    [self.detection_boxes, self.detection_scores, self.detection_classes, self.num_detections],
                    feed_dict={self.image_tensor: tf_image_input})

            detection_boxes   = np.squeeze(detection_boxes)
            detection_classes = np.squeeze(detection_classes)
            detection_scores  = np.squeeze(detection_scores)


            ret = None
            detection_threshold = 0.4

            # Find first detection of signal. It's labeled with number 10
            idx = -1
            for i, cl in enumerate(detection_classes.tolist()):
                if cl == 10:
                    idx = i;
                    break;

            if idx == -1:
                pass  # no signals detected
            elif detection_scores[idx] < detection_threshold:
                pass # we are not confident of detection
            else:
                dim = image.shape[0:2]
                box = self.from_normalized_dims__to_pixel(detection_boxes[idx], dim)
                box_h, box_w  = (box[2] - box[0], box[3]-box[1])
                if (box_h < 20) or (box_w < 20):
                    rospy.logwarn("Box too small")  
                    pass    # box too small 
                elif ( box_h/box_w < 1.6):
                    rospy.logwarn("Box wrong ratio: "+str(box))  
                    self.out=1
#                    pass    # wrong ratio
                    ret = box
                else:
                    if self.done==1:
                        self.out=0
                    rospy.loginfo('detected bounding box: {} conf: {}'.format(box, detection_scores[idx]))
                    ret = box

        return ret
        
    def from_normalized_dims__to_pixel(self, box, dim):
            height, width = dim[0], dim[1]
            box_pixel = [int(box[0]*height), int(box[1]*width), int(box[2]*height), int(box[3]*width)]
            return np.array(box_pixel)


    def draw_box(self, img, box):
        cv2.rectangle(img, (box[1],box[0]), (box[3],box[2]), (255,0,0), 5)

        return img




