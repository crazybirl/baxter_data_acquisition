# Copyright (c) 2016, BRML
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import numpy as np
import os
import rospy
from scipy.spatial import Delaunay
from tf import transformations

import baxter_interface
from baxter_pykdl import baxter_kinematics

from baxter_data_acquisition.settings import (
    joint_names,
    q_lim
)
from hdl import PoseConfigDuration


class PoseHandler(PoseConfigDuration):
    def __init__(self, file_name=None):
        """ A pose handler.
        :param file_name: A file containing a list of poses, where each row
        contains a pose as comma-separated entries.
        """
        super(PoseHandler, self).__init__()
        try:
            self._data = self.load_data(file_name=file_name)
        except IOError as e:
            print " => %s" % e
            print "Recording poses ..."
            path = raw_input("Where to save poses.txt and configurations.txt: ")
            if not os.path.exists(path):
                os.makedirs(path)
            self._data = self.record_poses(path=path)

    def get_closest_pose(self, pose):
        """ Find the closest pose from the list of poses to a given pose. Use
        Euclidean distance as metric.
        :param pose: The pose to find the closest pose to.
        :return: The index of the closest pose in the list of poses.
        """
        if not isinstance(pose, list) and len(pose) != 6:
            raise ValueError("Pose must be a list with 6 entries!")
        try:
            err = map(lambda x: np.sum(x**2), self._data - pose)
        except Exception:
            raise
        err = np.asarray(err)
        return np.argmin(err)

    def record_poses(self, path):
        """ Record limb poses and corresponding configurations.
        Move baxter's limb manually to a configuration in workspace and press
        'y' to record the current pose and the current set of joint angles.
        Press 'n' to stop recording poses.
        :param path: Where to save 'poses.txt' and 'configurations.txt'.
        :return: A numpy array containing the recorded poses as rows.
        """
        def _endpoint_pose():
            """ Current pose of the wrist of one arm of the baxter robot.
            :return: pose [x, y, z, a, b, c]
            """
            qp = limb.endpoint_pose()
            r = transformations.euler_from_quaternion(qp['orientation'])
            return [qp['position'][0], qp['position'][1], qp['position'][2],
                    r[0], r[1], r[2]]

        arm = raw_input(" Record poses for 'left' or 'right' arm: ")
        if arm not in ['left', 'right']:
            raise ValueError("Must be 'left' or 'right' arm!")
        limb = baxter_interface.Limb(arm)

        poses = list()
        cfgs = list()
        while not rospy.is_shutdown():
            key = raw_input("Record pose and configuration? (y, n): ")
            if key == 'y' or key == 'Y':
                print 'yes'
                pose = _endpoint_pose()
                cfg = limb.joint_angles()
                print 'pose', pose
                print 'cfg ', [cfg[jn] for jn in joint_names(arm)]
                try:
                    cfg_ik = self._inverse_kinematics(pose, arm)
                    poses.append(pose)
                    cfgs.append(cfg_ik)
                    print 'ikin', list(cfg_ik)
                except ValueError as e:
                    print "Failed to record pose due to ik failure. Repeat."
            elif key == 'n' or key == 'N':
                print 'Writing recorded poses and configurations ...'
                np.savetxt(os.path.join(path, 'poses.txt'), poses,
                           delimiter=',', header='x y z a b c')
                np.savetxt(os.path.join(path, 'configurations.txt'), cfgs,
                           delimiter=',', header='s0, s1, e0, e1, w0, w1, w2')
                return np.asarray(poses)

    def test_poses(self):
        """ Test poses in self._data by moving through them one after the
        other."""
        arm = raw_input(" Test poses for 'left' or 'right' arm: ")
        if arm not in ['left', 'right']:
            raise ValueError("Must be 'left' or 'right' arm!")
        limb = baxter_interface.Limb(arm)

        for idx in range(self._data.shape[0]):
            if rospy.is_shutdown():
                break
            cfg_ik = self._inverse_kinematics(self._data[idx, :], arm)
            cmd = dict(zip(joint_names(arm), cfg_ik))
            limb.move_to_joint_positions(cmd)

    def sample(self):
        """ Sample configurations within the workspace and store them. """
        arm = raw_input(" Sample configurations for 'left' or 'right' arm: ")
        if arm not in ['left', 'right']:
            raise ValueError("Must be 'left' or 'right' arm!")
        kin = baxter_kinematics(arm)

        hull = Delaunay(self._data[:, :3])

        n_configs = 300
        configs = np.empty((n_configs, 7))
        poses = np.empty((n_configs, 7))
        idx = 0
        lim = [q_lim(arm)[jn] for jn in joint_names(arm)]
        while idx < n_configs:
            try:
                poses[idx, :], configs[idx, :] = \
                    sample_from_workspace(hull, kin, lim, arm)
                idx += 1
            except ValueError:
                pass
        print "\n"
        path = raw_input(" Where to save poses2.txt and configurations2.txt: ")
        if not os.path.exists(path):
            os.makedirs(path)
        np.savetxt(os.path.join(path, 'configurations2.txt'), configs,
                   delimiter=',', header='s0, s1, e0, e1, w0, w1, w2')
        np.savetxt(os.path.join(path, 'poses2.txt'), poses,
                   delimiter=',', header='px, py, pz, ox, oy, oz, ow')


def sample_from_workspace(hull, kin, lim, arm):
    """ Sample Cartesian pose and corresponding seven-dimensional
    configuration within the workspace.
    :param hull: Delauny composition of the workspace.
    :param kin: A pykdl kinematic instance.
    :param lim: Dictionary of joint name keys to joint angle limit tuples.
    :param arm: <left/right> arm.
    :return: A tuple (pose, config).
    :raise: ValueError if pose does not lie within workspace.
    """
    # sample seven uniform random values
    config = np.random.random_sample((7,))
    # transform to joint range
    config = [config[i]*(lim[i][1] - lim[i][0]) + lim[i][0]
              for i in range(len(config))]
    cfg = {a: b for a, b in zip(joint_names(arm), config)}
    # transform to Cartesian space using forward kinematics
    pose = kin.forward_position_kinematics(joint_values=cfg)
    # check if pose is in convex hull of workspace corners
    if hull.find_simplex(pose[:3]) >= 0:
        return pose, config
    else:
        raise ValueError("Sampled pose does not lie in workspace!")
