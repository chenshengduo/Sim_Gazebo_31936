#!/usr/bin/env python
import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt
import gym
# from enviroment_PPO_house_v1 import Env
# from environment_PPO_v2 import Env
from environment_stage_v1 import Env

import rospy
import os
import numpy as np
import random
import time
import sys

from geometry_msgs.msg import Twist, Point, Pose
import time


class PPO(object):

    def __init__(self,a_lr,c_lr,s_dim,a_dim,aSteps,cSteps,method,gpu_options,complexNN):
        self.A_LR = a_lr
        self.C_LR = c_lr
        self.S_DIM = s_dim
        self.A_DIM = a_dim
        self.A_UPDATE_STEPS = aSteps
        self.C_UPDATE_STEPS = cSteps
        self.METHOD = method
        self.complexNN = complexNN
        self.sess = tf.Session(config=tf.ConfigProto(gpu_options=gpu_options))
        # self.sess = tf.Session()
        self.tfs = tf.placeholder(tf.float32, [None, self.S_DIM], 'state')

        # critic
        with tf.variable_scope('critic'):
            #################################two layer added by qinjielin##################################
            l2 = None
            hid1_size = self.S_DIM#self.S_DIM * 10  # first layer
            hid2_size = self.S_DIM#self.S_DIM * 10#int(np.sqrt(hid1_size * hid3_size)) # second layer
            if(self.complexNN):
                l1 = tf.layers.dense(self.tfs, hid1_size, tf.nn.relu,trainable=True) 
                l2 = tf.layers.dense(l1, hid2_size, tf.nn.relu,trainable=True)
            else:
                l2 = tf.layers.dense(self.tfs, self.S_DIM*10, tf.nn.relu)
            self.v = tf.layers.dense(l2, 1)
            self.tfdc_r = tf.placeholder(tf.float32, [None, 1], 'discounted_r')
            self.advantage = self.tfdc_r - self.v
            self.closs = tf.reduce_mean(tf.square(self.advantage))
            self.ctrain_op = tf.train.AdamOptimizer(self.C_LR).minimize(self.closs)

        # actor
        pi, pi_params = self._build_anet('pi', trainable=True)
        oldpi, oldpi_params = self._build_anet('oldpi', trainable=False)
        with tf.variable_scope('sample_action'):
            self.sample_op = tf.squeeze(pi.sample(1), axis=0)       # choosing action
        with tf.variable_scope('update_oldpi'):
            self.update_oldpi_op = [oldp.assign(p) for p, oldp in zip(pi_params, oldpi_params)]

        self.tfa = tf.placeholder(tf.float32, [None, self.A_DIM], 'action')
        self.tfadv = tf.placeholder(tf.float32, [None, 1], 'advantage')
        with tf.variable_scope('loss'):
            with tf.variable_scope('surrogate'):
                # ratio = tf.exp(pi.log_prob(self.tfa) - oldpi.log_prob(self.tfa))
                ratio = pi.prob(self.tfa) / oldpi.prob(self.tfa)
                surr = ratio * self.tfadv
            if self.METHOD['name'] == 'kl_pen':
                self.tflam = tf.placeholder(tf.float32, None, 'lambda')
                kl = tf.distributions.kl_divergence(oldpi, pi)
                self.kl_mean = tf.reduce_mean(kl)
                self.aloss = -(tf.reduce_mean(surr - self.tflam * kl))
            else:   # clipping method, find this is better
                self.aloss = -tf.reduce_mean(tf.minimum(
                    surr,
                    tf.clip_by_value(ratio, 1.-self.METHOD['epsilon'], 1.+self.METHOD['epsilon'])*self.tfadv))

        with tf.variable_scope('atrain'):
            self.atrain_op = tf.train.AdamOptimizer(self.A_LR).minimize(self.aloss)

        tf.summary.FileWriter("log/", self.sess.graph)

        self.sess.run(tf.global_variables_initializer())
        self.saver = tf.train.Saver(max_to_keep=20)#qinjielin 

    def update(self, s, a, r):
        self.sess.run(self.update_oldpi_op)
        adv = self.sess.run(self.advantage, {self.tfs: s, self.tfdc_r: r})
        # adv = (adv - adv.mean())/(adv.std()+1e-6)     # sometimes helpful

        # update actor
        if self.METHOD['name'] == 'kl_pen':
            for _ in range(self.A_UPDATE_STEPS):
                _, kl = self.sess.run(
                    [self.atrain_op, self.kl_mean],
                    {self.tfs: s, self.tfa: a, self.tfadv: adv, self.tflam: self.METHOD['lam']})
                if kl > 4*self.METHOD['kl_target']:  # this in in google's paper
                    break
            if kl < self.METHOD['kl_target'] / 1.5:  # adaptive lambda, this is in OpenAI's paper
                self.METHOD['lam'] /= 2
            elif kl > self.METHOD['kl_target'] * 1.5:
                self.METHOD['lam'] *= 2
            self.METHOD['lam'] = np.clip(self.METHOD['lam'], 1e-4, 10)    # sometimes explode, this clipping is my solution
        else:   # clipping method, find this is better (OpenAI's paper)
            [self.sess.run(self.atrain_op, {self.tfs: s, self.tfa: a, self.tfadv: adv}) for _ in range(self.A_UPDATE_STEPS)]

        # update critic
        [self.sess.run(self.ctrain_op, {self.tfs: s, self.tfdc_r: r}) for _ in range(self.C_UPDATE_STEPS)]

    def _build_anet(self, name, trainable):
        #################################three layer added by qinjielin##################################
        hid1_size =  self.S_DIM * 5#self.S_DIM * 10#self.S_DIM * 6  # first layer
        hid2_size =  self.S_DIM * 2#self.S_DIM * 10#int(np.sqrt(hid1_size * hid3_size)) # second layer
        with tf.variable_scope(name):
            l2 = None
            if(self.complexNN):
                l1 = tf.layers.dense(self.tfs, hid1_size, tf.nn.relu, trainable=trainable)
                l2 = tf.layers.dense(l1, hid2_size, tf.nn.relu, trainable=trainable)
                # l3 = tf.layers.dense(l2, hid3_size, tf.nn.relu, trainable=trainable)
            else:
                l2 = tf.layers.dense(self.tfs,  self.S_DIM * 10, tf.nn.relu, trainable=trainable)
            mu = 2 * tf.layers.dense(l2, self.A_DIM, tf.nn.tanh, trainable=trainable)
            sigma = tf.layers.dense(l2, self.A_DIM, tf.nn.softplus, trainable=trainable)
            norm_dist = tf.distributions.Normal(loc=mu, scale=sigma)
        params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=name)
        return norm_dist, params

    def choose_action(self, s):
        s = s[np.newaxis, :]
        a = self.sess.run(self.sample_op, {self.tfs: s})[0]
        if(itimateFlag):
            tempa = getTeleopCMd()
            a[0] = tempa[0]
            a[1] = tempa[1]
        return np.clip(a,-2,2)

    def get_v(self, s):
        if s.ndim < 2: s = s[np.newaxis, :]
        return self.sess.run(self.v, {self.tfs: s})[0, 0]

    def save_model(self,fileLoc,filename,step):
        # os_path = os.path.dirname(os.path.abspath(__file__))
        # cpath = os_path + '/saved_model/'+filename+'/PPO_model-'+str(step)+'.ckpt'
        cpath = fileLoc+filename+'/PPO_model-'+str(step)+'.ckpt'
        save_path = self.saver.save(self.sess, cpath)#qinjielin 
        print("Model saved in path: %s" % save_path)
        return

    def load_model(self,fileLoc,filename,step):
        # os_path = os.path.dirname(os.path.abspath(__file__))
        # cpath = os_path+'/saved_model/'+filename+'/PPO_model-'+str(step)+'.ckpt'
        cpath = fileLoc+filename+'/PPO_model-'+str(step)+'.ckpt'
        self.saver.restore(self.sess,cpath)#qinjielin
        print("model restore")
        return

def getTeleopCMd():
    data = None
    a = np.zeros(shape=(2,))
    while data is None:
        try:
            data = rospy.wait_for_message('humanCmd', Twist, timeout=1)
        except:
            # print("getting scan pass")
            pass
    a[0]=data.angular.z
    a[1]=data.linear.x
    max_linear_vel = 0.3
    a[1]=(data.linear.x/max_linear_vel) *4 - 2 
    return a

def writeData(fileLoc,dirName,ep,r,successTimes):
    # filename = os.path.dirname(os.path.abspath(__file__))+"/saved_model/"+dirName+"/data.txt"
    filename = fileLoc+dirName+"/data.txt"
    dirname =  os.path.dirname(filename)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    with open(filename, 'a') as filehandle:  
        filehandle.write('ep: %d reward: %d successful time: %d\n' % (ep,r,successTimes))

def writeInfo(fileLoc,dirName,info):
    # filename = os.path.dirname(os.path.abspath(__file__))+"/saved_model/"+dirName+"/data.txt"
    filename = fileLoc+dirName+"/data.txt"
    dirname =  os.path.dirname(filename)
    print("file Loc:",fileLoc)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    with open(filename, 'a') as filehandle:   
        filehandle.write('model info: %s \n' % (info))

def saveModelInfo(fileLoc,fileName):
    argNames =  ["dis_weight","lin_weight","obs_weight","ori_weight","col_weight","goal_weight","rot_weight","laser_step","ep_len","complex_nn","step_time"]
    argValues = getArgInfo(argNames)
    info = ""
    numParam = len(argNames)
    for i in range(numParam):
        info = info + " "+str(argNames[i])+":="+str(argValues[i])
    writeInfo(fileLoc,fileName,info)

def getArgInfo(argNames):
    res = []
    for argName in argNames:
        weight = rospy.get_param('/%s'%argName,0.0)
        res.append(weight)
    return res

if __name__ == "__main__":
    rospy.init_node('turtlebot3_PPO_house_v1')
    trainFlag = rospy.get_param('/train')
    loadFalg =  rospy.get_param('/load')
    filename = rospy.get_param('/filename')
    load_ep = rospy.get_param('/load_ep')
    ep_len = rospy.get_param('/ep_len')
    itimateFlag = rospy.get_param('/itimateFlag')
    laserStep = rospy.get_param('/laser_step')
    complexNN = rospy.get_param('/complex_nn')
    fileLoc = os.path.dirname(os.path.abspath(__file__))+"/../../../../../../clever/saved_model_lin/"
    # fileLoc = os.path.dirname(os.path.abspath(__file__))+"/../saved_model/"
    if( not loadFalg):
        if(trainFlag):
            saveModelInfo(fileLoc,filename)
    if(int(complexNN) == 0):
        complexNN = False
    else:
        complexNN = True

    #####################training setting############################
    EP_MAX = 1000#800
    EP_LEN = int(ep_len)#ep_len#200
    GAMMA = 0.9
    BATCH =  32#8#32 QinjieLin
    model_gap = 25 #save model every 30 eps

    gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.02)

    #########################PPO setting#####################
    A_LR = 0.0001
    C_LR = 0.0002
    A_UPDATE_STEPS = 10#10
    C_UPDATE_STEPS = 10#10
    S_DIM, A_DIM = int(360/laserStep)+4, 2  #QinjieLin 16,2
    METHOD = [
        dict(name='kl_pen', kl_target=0.01, lam=0.5),   # KL penalty
        dict(name='clip', epsilon=0.2),                 # Clipped surrogate objective, find this is better
    ][1]        # choose the method for optimization



    #######################training###########################
    env = Env()
    ppo = PPO(a_lr=A_LR,c_lr = C_LR,aSteps = A_UPDATE_STEPS,cSteps = C_UPDATE_STEPS,s_dim = S_DIM,a_dim=A_DIM,method =METHOD,gpu_options = gpu_options,complexNN=complexNN)
    if( loadFalg):
        ppo.load_model(fileLoc,filename,load_ep)
    all_ep_r = []

    for ep in range(EP_MAX):
        s = env.reset()
        buffer_s, buffer_a, buffer_r = [], [], []
        ep_r = 0
        for t in range(EP_LEN):    # in one episode
            a = ppo.choose_action(s)
            # print("ep:",t,"state:",np.round(s,2))
            # print("ep:",t,"choose action:",np.round(a,3))
            s_, r, done = env.step(a)
            buffer_s.append(s)
            buffer_a.append(a)
            buffer_r.append(r)   
            s = s_
            ep_r += r

            # update ppo
            if (t+1) % BATCH == 0 or t == EP_LEN-1:
                v_s_ = ppo.get_v(s_)
                discounted_r = []
                for r in buffer_r[::-1]:
                    v_s_ = r + GAMMA * v_s_
                    discounted_r.append(v_s_)
                discounted_r.reverse()

                bs, ba, br = np.vstack(buffer_s), np.vstack(buffer_a), np.array(discounted_r)[:, np.newaxis]
                buffer_s, buffer_a, buffer_r = [], [], []
                if(trainFlag):
                    ppo.update(bs, ba, br)

            #qinjieLin reset when collision
            if(done):
                env.reset()
        
        if(trainFlag):
            saveEp = ep
            if(loadFalg): 
                load_ep = load_ep + 1
                saveEp = load_ep
            if((saveEp%model_gap)==0):
                ppo.save_model(fileLoc,filename,saveEp)
        if ep == 0: all_ep_r.append(ep_r)
        else: all_ep_r.append(all_ep_r[-1]*0.9 + ep_r*0.1)

        #save ep reward data
        if(trainFlag):
            dataEp = ep
            if(loadFalg):
                dataEp = load_ep
            writeData(fileLoc,filename,dataEp,all_ep_r[-1],env.numSuccess)
        print(
            'Ep: %i' % ep,
            "|Ep_r: %i" % ep_r,
            ("|Lam: %.4f" % METHOD['lam']) if METHOD['name'] == 'kl_pen' else '',
        )
        print('Ep: %i' % ep,
            "model is saved in %s"% filename,
            "success time: %i" %env.numSuccess)

    plt.plot(np.arange(len(all_ep_r)), all_ep_r)
    plt.title("model %s"%filename)
    plt.xlabel('Episode');plt.ylabel('Moving averaged episode reward');plt.show()