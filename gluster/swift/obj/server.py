# Copyright (c) 2012-2014 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

""" Object Server for Gluster for Swift """

# Simply importing this monkey patches the constraint handling to fit our
# needs
import gluster.swift.common.constraints    # noqa
from swift.common.swob import HTTPConflict
from swift.common.utils import public, timing_stats
from gluster.swift.common.exceptions import AlreadyExistsAsFile, \
    AlreadyExistsAsDir
from swift.common.request_helpers import split_and_validate_path

from swift.obj import server

from gluster.swift.obj.diskfile import OnDiskManager

import os
from swift.common.exceptions import ConnectionTimeout
from swift.common.bufferedhttp import http_connect
from eventlet import Timeout
from swift.common.http import is_success
from swift.common.manager import SWIFT_DIR
from gluster.swift.common.ring import Ring
from swift import gettext_ as _


class ObjectController(server.ObjectController):
    """
    Subclass of the object server's ObjectController which replaces the
    container_update method with one that is a no-op (information is simply
    stored on disk and already updated by virtue of performing the file system
    operations directly).
    """
    def setup(self, conf):
        """
        Implementation specific setup. This method is called at the very end
        by the constructor to allow a specific implementation to modify
        existing attributes or add its own attributes.

        :param conf: WSGI configuration parameter
        """
        # Common on-disk hierarchy shared across account, container and object
        # servers.
        self._ondisk_mgr = OnDiskManager(conf, self.logger)
        self.swift_dir = conf.get('swift_dir', SWIFT_DIR)

    def get_diskfile(self, device, partition, account, container, obj,
                     **kwargs):
        """
        Utility method for instantiating a DiskFile object supporting a given
        REST API.

        An implementation of the object server that wants to use a different
        DiskFile class would simply over-ride this method to provide that
        behavior.
        """
        return self._ondisk_mgr.get_diskfile(device, account, container, obj,
                                             **kwargs)

    def container_update(self, *args, **kwargs):
        """
        Update the container when objects are updated.

        For Gluster, this is just a no-op, since a container is just the
        directory holding all the objects (sub-directory hierarchy of files).
        """
        return

    def get_object_ring(self):
        if hasattr(self, 'object_ring'):
            if not self.object_ring:
                self.object_ring = Ring(self.swift_dir, ring_name='object')
        else:
            self.object_ring = Ring(self.swift_dir, ring_name='object')
        return self.object_ring

    def async_update(self, op, account, container, obj, host, partition,
                     contdevice, headers_out, objdevice):
        """
        In Openstack Swift, this method is called by:
            * container_update (a no-op in gluster-swift)
            * delete_at_update (to PUT objects into .expiring_objects account)

        The Swift's version of async_update only sends the request to
        container-server to PUT the object. The container-server calls
        container_update method which makes an entry for the object in it's
        database. No actual object is created on disk.

        But in gluster-swift container_update is a no-op, so we'll
        have to PUT an actual object. We override async_update to create a
        container first and then the corresponding "tracker object" which
        tracks expired objects scheduled for deletion.
        """

        headers_out['user-agent'] = 'obj-server %s' % os.getpid()
        if all([host, partition, contdevice]):
            # PUT the container. Send request directly to container-server
            container_path = '/%s/%s' % (account, container)
            try:
                with ConnectionTimeout(self.conn_timeout):
                    ip, port = host.rsplit(':', 1)
                    conn = http_connect(ip, port, contdevice, partition, op,
                                        container_path, headers_out)
                with Timeout(self.node_timeout):
                    response = conn.getresponse()
                    response.read()
                    if not is_success(response.status):
                        self.logger.error(_(
                            'async_update : '
                            'ERROR Container update failed :%(status)d '
                            'response from %(ip)s:%(port)s/%(dev)s'),
                            {'status': response.status, 'ip': ip, 'port': port,
                             'dev': contdevice})
                        return
            except (Exception, Timeout):
                self.logger.exception(_(
                    'async_update : '
                    'ERROR Container update failed :%(ip)s:%(port)s/%(dev)s'),
                    {'ip': ip, 'port': port, 'dev': contdevice})

            # PUT the tracker object. Send request directly to object-server
            object_path = '/%s/%s/%s' % (account, container, obj)
            headers_out['Content-Length'] = 0
            headers_out['Content-Type'] = 'text/plain'
            try:
                with ConnectionTimeout(self.conn_timeout):
                    # FIXME: Assuming that get_nodes returns single node
                    part, nodes = self.get_object_ring().get_nodes(account,
                                                                   container,
                                                                   obj)
                    ip = nodes[0]['ip']
                    port = nodes[0]['port']
                    objdevice = nodes[0]['device']
                    conn = http_connect(ip, port, objdevice, partition, op,
                                        object_path, headers_out)
                with Timeout(self.node_timeout):
                    response = conn.getresponse()
                    response.read()
                    if is_success(response.status):
                        return
                    else:
                        self.logger.error(_(
                            'async_update : '
                            'ERROR Object PUT failed : %(status)d '
                            'response from %(ip)s:%(port)s/%(dev)s'),
                            {'status': response.status, 'ip': ip, 'port': port,
                             'dev': objdevice})
            except (Exception, Timeout):
                self.logger.exception(_(
                    'async_update : '
                    'ERROR Object PUT failed :%(ip)s:%(port)s/%(dev)s'),
                    {'ip': ip, 'port': port, 'dev': objdevice})
        return

    @public
    @timing_stats()
    def PUT(self, request):
        try:
            return server.ObjectController.PUT(self, request)
        except (AlreadyExistsAsFile, AlreadyExistsAsDir):
            device = \
                split_and_validate_path(request, 1, 5, True)
            return HTTPConflict(drive=device, request=request)


def app_factory(global_conf, **local_conf):
    """paste.deploy app factory for creating WSGI object server apps"""
    conf = global_conf.copy()
    conf.update(local_conf)
    return ObjectController(conf)
