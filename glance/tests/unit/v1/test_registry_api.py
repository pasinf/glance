# vim: tabstop=4 shiftwidth=4 softtabstop=4
# -*- coding: utf-8 -*-

# Copyright 2010-2011 OpenStack, LLC
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import datetime
import json

from oslo.config import cfg
import routes
from sqlalchemy import exc
import stubout
import webob

import glance.api.common
import glance.common.config
import glance.context
from glance.db.sqlalchemy import api as db_api
from glance.db.sqlalchemy import models as db_models
from glance.openstack.common import timeutils
from glance.openstack.common import uuidutils
from glance.registry.api import v1 as rserver
import glance.store.filesystem
from glance.tests.unit import base
from glance.tests import utils as test_utils

CONF = cfg.CONF

_gen_uuid = uuidutils.generate_uuid

UUID1 = _gen_uuid()
UUID2 = _gen_uuid()


class TestRegistryDb(test_utils.BaseTestCase):

    def setUp(self):
        """Establish a clean test environment"""
        super(TestRegistryDb, self).setUp()
        self.stubs = stubout.StubOutForTesting()
        self.orig_engine = db_api._ENGINE
        self.orig_connection = db_api._CONNECTION
        self.orig_maker = db_api._MAKER
        self.addCleanup(self.stubs.UnsetAll)
        self.addCleanup(setattr, db_api, '_ENGINE', self.orig_engine)
        self.addCleanup(setattr, db_api, '_CONNECTION', self.orig_connection)
        self.addCleanup(setattr, db_api, '_MAKER', self.orig_maker)

    def test_bad_sql_connection(self):
        """
        Test that a bad sql_connection option supplied to the registry
        API controller results in a) an Exception being thrown and b)
        a message being logged to the registry log file...
        """
        self.config(verbose=True, debug=True, sql_connection='baddriver:///')

        # We set this to None to trigger a reconfigure, otherwise
        # other modules may have already correctly configured the DB
        db_api._ENGINE = None
        db_api._CONNECTION = None
        db_api._MAKER = None
        db_api.setup_db_env()
        self.assertRaises((ImportError, exc.ArgumentError),
                          db_api.get_engine)
        exc_raised = False
        self.log_written = False

        def fake_log_error(msg):
            if 'Error configuring registry database' in msg:
                self.log_written = True

        self.stubs.Set(db_api.LOG, 'error', fake_log_error)
        try:
            api_obj = rserver.API(routes.Mapper())
            api = test_utils.FakeAuthMiddleware(api_obj, is_admin=True)
            req = webob.Request.blank('/images/%s' % _gen_uuid())
            res = req.get_response(api)
        except exc.ArgumentError:
            exc_raised = True
        except ImportError:
            exc_raised = True

        self.assertTrue(exc_raised)
        self.assertTrue(self.log_written)


class TestRegistryAPI(base.IsolatedUnitTest):
    def setUp(self):
        """Establish a clean test environment"""
        super(TestRegistryAPI, self).setUp()
        self.mapper = routes.Mapper()
        self.api = test_utils.FakeAuthMiddleware(rserver.API(self.mapper),
                                                 is_admin=True)
        self.FIXTURES = [
            {'id': UUID1,
             'name': 'fake image #1',
             'status': 'active',
             'disk_format': 'ami',
             'container_format': 'ami',
             'is_public': False,
             'created_at': timeutils.utcnow(),
             'updated_at': timeutils.utcnow(),
             'deleted_at': None,
             'deleted': False,
             'checksum': None,
             'min_disk': 0,
             'min_ram': 0,
             'size': 13,
             'locations': ["file:///%s/%s" % (self.test_dir, UUID1)],
             'properties': {'type': 'kernel'}},
            {'id': UUID2,
             'name': 'fake image #2',
             'status': 'active',
             'disk_format': 'vhd',
             'container_format': 'ovf',
             'is_public': True,
             'created_at': timeutils.utcnow(),
             'updated_at': timeutils.utcnow(),
             'deleted_at': None,
             'deleted': False,
             'checksum': None,
             'min_disk': 5,
             'min_ram': 256,
             'size': 19,
             'locations': ["file:///%s/%s" % (self.test_dir, UUID2)],
             'properties': {}}]
        self.context = glance.context.RequestContext(is_admin=True)
        db_api.setup_db_env()
        db_api.get_engine()
        self.destroy_fixtures()
        self.create_fixtures()

    def tearDown(self):
        """Clear the test environment"""
        super(TestRegistryAPI, self).tearDown()
        self.destroy_fixtures()

    def create_fixtures(self):
        for fixture in self.FIXTURES:
            db_api.image_create(self.context, fixture)
            # We write a fake image file to the filesystem
            with open("%s/%s" % (self.test_dir, fixture['id']), 'wb') as image:
                image.write("chunk00000remainder")
                image.flush()

    def destroy_fixtures(self):
        # Easiest to just drop the models and re-create them...
        db_models.unregister_models(db_api._ENGINE)
        db_models.register_models(db_api._ENGINE)

    def test_show(self):
        """
        Tests that the /images/<id> registry API endpoint
        returns the expected image
        """
        fixture = {'id': UUID2,
                   'name': 'fake image #2',
                   'size': 19,
                   'min_ram': 256,
                   'min_disk': 5,
                   'checksum': None}
        req = webob.Request.blank('/images/%s' % UUID2)
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)
        image = res_dict['image']
        for k, v in fixture.iteritems():
            self.assertEquals(v, image[k])

    def test_show_unknown(self):
        """
        Tests that the /images/<id> registry API endpoint
        returns a 404 for an unknown image id
        """
        req = webob.Request.blank('/images/%s' % _gen_uuid())
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 404)

    def test_show_invalid(self):
        """
        Tests that the /images/<id> registry API endpoint
        returns a 404 for an invalid (therefore unknown) image id
        """
        req = webob.Request.blank('/images/%s' % _gen_uuid())
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 404)

    def test_show_deleted_image_as_admin(self):
        """
        Tests that the /images/<id> registry API endpoint
        returns a 200 for deleted image to admin user.
        """
        # Delete image #2
        req = webob.Request.blank('/images/%s' % UUID2)
        req.method = 'DELETE'
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)

        req = webob.Request.blank('/images/%s' % UUID2)
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)

    def test_show_deleted_image_as_nonadmin(self):
        """
        Tests that the /images/<id> registry API endpoint
        returns a 404 for deleted image to non-admin user.
        """
        # Delete image #2
        req = webob.Request.blank('/images/%s' % UUID2)
        req.method = 'DELETE'
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)

        api = test_utils.FakeAuthMiddleware(rserver.API(self.mapper),
                                            is_admin=False)
        req = webob.Request.blank('/images/%s' % UUID2)
        res = req.get_response(api)
        self.assertEquals(res.status_int, 404)

    def test_get_root(self):
        """
        Tests that the root registry API returns "index",
        which is a list of public images
        """
        fixture = {'id': UUID2,
                   'name': 'fake image #2',
                   'size': 19,
                   'checksum': None}
        req = webob.Request.blank('/')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        for k, v in fixture.iteritems():
            self.assertEquals(v, images[0][k])

    def test_get_index(self):
        """
        Tests that the /images registry API returns list of
        public images
        """
        fixture = {'id': UUID2,
                   'name': 'fake image #2',
                   'size': 19,
                   'checksum': None}
        req = webob.Request.blank('/images')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        for k, v in fixture.iteritems():
            self.assertEquals(v, images[0][k])

    def test_get_index_marker(self):
        """
        Tests that the /images registry API returns list of
        public images that conforms to a marker query param
        """
        time1 = timeutils.utcnow() + datetime.timedelta(seconds=5)
        time2 = timeutils.utcnow() + datetime.timedelta(seconds=4)
        time3 = timeutils.utcnow()

        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 19,
                         'checksum': None,
                         'created_at': time1}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None,
                         'created_at': time2}

        db_api.image_create(self.context, extra_fixture)

        UUID5 = _gen_uuid()
        extra_fixture = {'id': UUID5,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None,
                         'created_at': time3}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images?marker=%s' % UUID4)
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        # should be sorted by created_at desc, id desc
        # page should start after marker 4
        self.assertEquals(len(images), 2)
        self.assertEquals(images[0]['id'], UUID5)
        self.assertEquals(images[1]['id'], UUID2)

    def test_get_index_unknown_marker(self):
        """
        Tests that the /images registry API returns a 400
        when an unknown marker is provided
        """
        req = webob.Request.blank('/images?marker=%s' % _gen_uuid())
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 400)

    def test_get_index_malformed_marker(self):
        """
        Tests that the /images registry API returns a 400
        when a malformed marker is provided
        """
        req = webob.Request.blank('/images?marker=4')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 400)
        self.assertTrue('marker' in res.body)

    def test_get_index_limit(self):
        """
        Tests that the /images registry API returns list of
        public images that conforms to a limit query param
        """
        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images?limit=1')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        # expect list to be sorted by created_at desc
        self.assertTrue(images[0]['id'], UUID4)

    def test_get_index_limit_negative(self):
        """
        Tests that the /images registry API returns list of
        public images that conforms to a limit query param
        """
        req = webob.Request.blank('/images?limit=-1')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 400)

    def test_get_index_limit_non_int(self):
        """
        Tests that the /images registry API returns list of
        public images that conforms to a limit query param
        """
        req = webob.Request.blank('/images?limit=a')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 400)

    def test_get_index_limit_marker(self):
        """
        Tests that the /images registry API returns list of
        public images that conforms to limit and marker query params
        """
        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images?marker=%s&limit=1' % UUID3)
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        # expect list to be sorted by created_at desc
        self.assertEqual(images[0]['id'], UUID2)

    def test_get_index_filter_name(self):
        """
        Tests that the /images registry API returns list of
        public images that have a specific name. This is really a sanity
        check, filtering is tested more in-depth using /images/detail
        """
        fixture = {'id': UUID2,
                   'name': 'fake image #2',
                   'size': 19,
                   'checksum': None}

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images?name=new name! #123')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 2)

        for image in images:
            self.assertEqual('new name! #123', image['name'])

    def test_get_index_sort_default_created_at_desc(self):
        """
        Tests that the /images registry API returns list of
        public images that conforms to a default sort key/dir
        """
        time1 = timeutils.utcnow() + datetime.timedelta(seconds=5)
        time2 = timeutils.utcnow() + datetime.timedelta(seconds=4)
        time3 = timeutils.utcnow()

        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 19,
                         'checksum': None,
                         'created_at': time1}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None,
                         'created_at': time2}

        db_api.image_create(self.context, extra_fixture)

        UUID5 = _gen_uuid()
        extra_fixture = {'id': UUID5,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None,
                         'created_at': time3}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 4)
        self.assertEquals(images[0]['id'], UUID3)
        self.assertEquals(images[1]['id'], UUID4)
        self.assertEquals(images[2]['id'], UUID5)
        self.assertEquals(images[3]['id'], UUID2)

    def test_get_index_bad_sort_key(self):
        """Ensure a 400 is returned when a bad sort_key is provided."""
        req = webob.Request.blank('/images?sort_key=asdf')
        res = req.get_response(self.api)
        self.assertEqual(400, res.status_int)

    def test_get_index_bad_sort_dir(self):
        """Ensure a 400 is returned when a bad sort_dir is provided."""
        req = webob.Request.blank('/images?sort_dir=asdf')
        res = req.get_response(self.api)
        self.assertEqual(400, res.status_int)

    def test_get_index_sort_name_asc(self):
        """
        Tests that the /images registry API returns list of
        public images sorted alphabetically by name in
        ascending order.
        """
        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'asdf',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'xyz',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images?sort_key=name&sort_dir=asc')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)

        images = res_dict['images']
        self.assertEquals(len(images), 3)
        self.assertEquals(images[0]['id'], UUID3)
        self.assertEquals(images[1]['id'], UUID2)
        self.assertEquals(images[2]['id'], UUID4)

    def test_get_index_sort_status_desc(self):
        """
        Tests that the /images registry API returns list of
        public images sorted alphabetically by status in
        descending order.
        """
        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'queued',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'asdf',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'xyz',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images?sort_key=status&sort_dir=desc')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)

        images = res_dict['images']
        self.assertEquals(len(images), 3)
        self.assertEquals(images[0]['id'], UUID3)
        self.assertEquals(images[1]['id'], UUID4)
        self.assertEquals(images[2]['id'], UUID2)

    def test_get_index_sort_disk_format_asc(self):
        """
        Tests that the /images registry API returns list of
        public images sorted alphabetically by disk_format in
        ascending order.
        """
        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'asdf',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vdi',
                         'container_format': 'ovf',
                         'name': 'xyz',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images?sort_key=disk_format&sort_dir=asc')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)

        images = res_dict['images']
        self.assertEquals(len(images), 3)
        self.assertEquals(images[0]['id'], UUID3)
        self.assertEquals(images[1]['id'], UUID4)
        self.assertEquals(images[2]['id'], UUID2)

    def test_get_index_sort_container_format_desc(self):
        """
        Tests that the /images registry API returns list of
        public images sorted alphabetically by container_format in
        descending order.
        """
        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'asdf',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'iso',
                         'container_format': 'bare',
                         'name': 'xyz',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        url = '/images?sort_key=container_format&sort_dir=desc'
        req = webob.Request.blank(url)
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)

        images = res_dict['images']
        self.assertEquals(len(images), 3)
        self.assertEquals(images[0]['id'], UUID2)
        self.assertEquals(images[1]['id'], UUID4)
        self.assertEquals(images[2]['id'], UUID3)

    def test_get_index_sort_size_asc(self):
        """
        Tests that the /images registry API returns list of
        public images sorted by size in ascending order.
        """
        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'asdf',
                         'size': 100,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'iso',
                         'container_format': 'bare',
                         'name': 'xyz',
                         'size': 2,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        url = '/images?sort_key=size&sort_dir=asc'
        req = webob.Request.blank(url)
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)

        images = res_dict['images']
        self.assertEquals(len(images), 3)
        self.assertEquals(images[0]['id'], UUID4)
        self.assertEquals(images[1]['id'], UUID2)
        self.assertEquals(images[2]['id'], UUID3)

    def test_get_index_sort_created_at_asc(self):
        """
        Tests that the /images registry API returns list of
        public images sorted by created_at in ascending order.
        """
        now = timeutils.utcnow()
        time1 = now + datetime.timedelta(seconds=5)
        time2 = now

        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 19,
                         'checksum': None,
                         'created_at': time1}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None,
                         'created_at': time2}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images?sort_key=created_at&sort_dir=asc')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)

        images = res_dict['images']
        self.assertEquals(len(images), 3)
        self.assertEquals(images[0]['id'], UUID2)
        self.assertEquals(images[1]['id'], UUID4)
        self.assertEquals(images[2]['id'], UUID3)

    def test_get_index_sort_updated_at_desc(self):
        """
        Tests that the /images registry API returns list of
        public images sorted by updated_at in descending order.
        """
        now = timeutils.utcnow()
        time1 = now + datetime.timedelta(seconds=5)
        time2 = now

        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 19,
                         'checksum': None,
                         'created_at': None,
                         'updated_at': time1}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None,
                         'created_at': None,
                         'updated_at': time2}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images?sort_key=updated_at&sort_dir=desc')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)

        images = res_dict['images']
        self.assertEquals(len(images), 3)
        self.assertEquals(images[0]['id'], UUID3)
        self.assertEquals(images[1]['id'], UUID4)
        self.assertEquals(images[2]['id'], UUID2)

    def test_get_details(self):
        """
        Tests that the /images/detail registry API returns
        a mapping containing a list of detailed image information
        """
        fixture = {'id': UUID2,
                   'name': 'fake image #2',
                   'is_public': True,
                   'size': 19,
                   'min_disk': 5,
                   'min_ram': 256,
                   'checksum': None,
                   'disk_format': 'vhd',
                   'container_format': 'ovf',
                   'status': 'active'}

        req = webob.Request.blank('/images/detail')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        for k, v in fixture.iteritems():
            self.assertEquals(v, images[0][k])

    def test_get_details_limit_marker(self):
        """
        Tests that the /images/details registry API returns list of
        public images that conforms to limit and marker query params.
        This functionality is tested more thoroughly on /images, this is
        just a sanity check
        """
        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?marker=%s&limit=1' % UUID3)
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        # expect list to be sorted by created_at desc
        self.assertEqual(images[0]['id'], UUID2)

    def test_get_details_invalid_marker(self):
        """
        Tests that the /images/detail registry API returns a 400
        when an invalid marker is provided
        """
        req = webob.Request.blank('/images/detail?marker=%s'
                                  % _gen_uuid())
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 400)

    def test_get_details_filter_name(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a specific name
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'new name! #123',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?name=new name! #123')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 2)

        for image in images:
            self.assertEqual('new name! #123', image['name'])

    def test_get_details_filter_status(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a specific status
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'saving',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #4',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?status=saving')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        for image in images:
            self.assertEqual('saving', image['status'])

    def test_get_details_filter_container_format(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a specific container_format
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vdi',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #4',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?container_format=ovf')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 2)

        for image in images:
            self.assertEqual('ovf', image['container_format'])

    def test_get_details_filter_min_disk(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a specific min_disk
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 19,
                         'min_disk': 7,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #4',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?min_disk=7')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        for image in images:
            self.assertEqual(7, image['min_disk'])

    def test_get_details_filter_min_ram(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a specific min_ram
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 19,
                         'min_ram': 514,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #4',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?min_ram=514')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        for image in images:
            self.assertEqual(514, image['min_ram'])

    def test_get_details_filter_disk_format(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a specific disk_format
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #4',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?disk_format=vhd')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 2)

        for image in images:
            self.assertEqual('vhd', image['disk_format'])

    def test_get_details_filter_size_min(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a size greater than or equal to size_min
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 18,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #4',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?size_min=19')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 2)

        for image in images:
            self.assertTrue(image['size'] >= 19)

    def test_get_details_filter_size_max(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a size less than or equal to size_max
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 18,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #4',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?size_max=19')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 2)

        for image in images:
            self.assertTrue(image['size'] <= 19)

    def test_get_details_filter_size_min_max(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a size less than or equal to size_max
        and greater than or equal to size_min
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 18,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #4',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #5',
                         'size': 6,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?size_min=18&size_max=19')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 2)

        for image in images:
            self.assertTrue(image['size'] <= 19 and image['size'] >= 18)

    def test_get_details_filter_changes_since(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a size less than or equal to size_max
        """
        dt1 = timeutils.utcnow() - datetime.timedelta(1)
        iso1 = timeutils.isotime(dt1)

        dt2 = timeutils.utcnow() + datetime.timedelta(1)
        iso2 = timeutils.isotime(dt2)

        image_ts = timeutils.utcnow() + datetime.timedelta(2)
        hour_before = image_ts.strftime('%Y-%m-%dT%H:%M:%S%%2B01:00')
        hour_after = image_ts.strftime('%Y-%m-%dT%H:%M:%S-01:00')

        dt4 = timeutils.utcnow() + datetime.timedelta(3)
        iso4 = timeutils.isotime(dt4)

        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 18,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)
        db_api.image_destroy(self.context, UUID3)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #4',
                         'size': 20,
                         'checksum': None,
                         'created_at': image_ts,
                         'updated_at': image_ts}

        db_api.image_create(self.context, extra_fixture)

        # Check a standard list, 4 images in db (2 deleted)
        req = webob.Request.blank('/images/detail')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)
        images = res_dict['images']
        self.assertEquals(len(images), 2)
        self.assertEqual(images[0]['id'], UUID4)
        self.assertEqual(images[1]['id'], UUID2)

        # Expect 3 images (1 deleted)
        req = webob.Request.blank('/images/detail?changes-since=%s' % iso1)
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)
        images = res_dict['images']
        self.assertEquals(len(images), 3)
        self.assertEqual(images[0]['id'], UUID4)
        self.assertEqual(images[1]['id'], UUID3)  # deleted
        self.assertEqual(images[2]['id'], UUID2)

        # Expect 1 images (0 deleted)
        req = webob.Request.blank('/images/detail?changes-since=%s' % iso2)
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)
        images = res_dict['images']
        self.assertEquals(len(images), 1)
        self.assertEqual(images[0]['id'], UUID4)

        # Expect 1 images (0 deleted)
        req = webob.Request.blank('/images/detail?changes-since=%s' %
                                  hour_before)
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)
        images = res_dict['images']
        self.assertEquals(len(images), 1)
        self.assertEqual(images[0]['id'], UUID4)

        # Expect 0 images (0 deleted)
        req = webob.Request.blank('/images/detail?changes-since=%s' %
                                  hour_after)
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)
        images = res_dict['images']
        self.assertEquals(len(images), 0)

        # Expect 0 images (0 deleted)
        req = webob.Request.blank('/images/detail?changes-since=%s' % iso4)
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)
        images = res_dict['images']
        self.assertEquals(len(images), 0)

        # Bad request (empty changes-since param)
        req = webob.Request.blank('/images/detail?changes-since=')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 400)

        # Bad request (invalid changes-since param)
        req = webob.Request.blank('/images/detail?changes-since=2011-09-05')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 400)

    def test_get_details_filter_property(self):
        """
        Tests that the /images/detail registry API returns list of
        public images that have a specific custom property
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 19,
                         'checksum': None,
                         'properties': {'prop_123': 'v a'}}

        db_api.image_create(self.context, extra_fixture)

        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'ami',
                         'container_format': 'ami',
                         'name': 'fake image #4',
                         'size': 19,
                         'checksum': None,
                         'properties': {'prop_123': 'v b'}}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?property-prop_123=v%20a')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        for image in images:
            self.assertEqual('v a', image['properties']['prop_123'])

    def test_get_details_filter_public_none(self):
        """
        Tests that the /images/detail registry API returns list of
        all images if is_public none is passed
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': False,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 18,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?is_public=None')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 3)

    def test_get_details_filter_public_false(self):
        """
        Tests that the /images/detail registry API returns list of
        private images if is_public false is passed
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': False,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 18,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?is_public=False')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 2)

        for image in images:
            self.assertEqual(False, image['is_public'])

    def test_get_details_filter_public_true(self):
        """
        Tests that the /images/detail registry API returns list of
        public images if is_public true is passed (same as default)
        """
        extra_fixture = {'id': _gen_uuid(),
                         'status': 'active',
                         'is_public': False,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'fake image #3',
                         'size': 18,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?is_public=True')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        images = res_dict['images']
        self.assertEquals(len(images), 1)

        for image in images:
            self.assertEqual(True, image['is_public'])

    def test_get_details_sort_name_asc(self):
        """
        Tests that the /images/details registry API returns list of
        public images sorted alphabetically by name in
        ascending order.
        """
        UUID3 = _gen_uuid()
        extra_fixture = {'id': UUID3,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'asdf',
                         'size': 19,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        UUID4 = _gen_uuid()
        extra_fixture = {'id': UUID4,
                         'status': 'active',
                         'is_public': True,
                         'disk_format': 'vhd',
                         'container_format': 'ovf',
                         'name': 'xyz',
                         'size': 20,
                         'checksum': None}

        db_api.image_create(self.context, extra_fixture)

        req = webob.Request.blank('/images/detail?sort_key=name&sort_dir=asc')
        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)
        res_dict = json.loads(res.body)

        images = res_dict['images']
        self.assertEquals(len(images), 3)
        self.assertEquals(images[0]['id'], UUID3)
        self.assertEquals(images[1]['id'], UUID2)
        self.assertEquals(images[2]['id'], UUID4)

    def test_create_image(self):
        """Tests that the /images POST registry API creates the image"""
        fixture = {'name': 'fake public image',
                   'is_public': True,
                   'disk_format': 'vhd',
                   'container_format': 'ovf'}

        req = webob.Request.blank('/images')

        req.method = 'POST'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)

        self.assertEquals(res.status_int, 200)

        res_dict = json.loads(res.body)

        for k, v in fixture.iteritems():
            self.assertEquals(v, res_dict['image'][k])

        # Test status was updated properly
        self.assertEquals('active', res_dict['image']['status'])

    def test_create_image_with_min_disk(self):
        """Tests that the /images POST registry API creates the image"""
        fixture = {'name': 'fake public image',
                   'is_public': True,
                   'min_disk': 5,
                   'disk_format': 'vhd',
                   'container_format': 'ovf'}

        req = webob.Request.blank('/images')

        req.method = 'POST'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)

        self.assertEquals(res.status_int, 200)

        res_dict = json.loads(res.body)

        self.assertEquals(5, res_dict['image']['min_disk'])

    def test_create_image_with_min_ram(self):
        """Tests that the /images POST registry API creates the image"""
        fixture = {'name': 'fake public image',
                   'is_public': True,
                   'min_ram': 256,
                   'disk_format': 'vhd',
                   'container_format': 'ovf'}

        req = webob.Request.blank('/images')

        req.method = 'POST'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)

        self.assertEquals(res.status_int, 200)

        res_dict = json.loads(res.body)

        self.assertEquals(256, res_dict['image']['min_ram'])

    def test_create_image_with_min_ram_default(self):
        """Tests that the /images POST registry API creates the image"""
        fixture = {'name': 'fake public image',
                   'is_public': True,
                   'disk_format': 'vhd',
                   'container_format': 'ovf'}

        req = webob.Request.blank('/images')

        req.method = 'POST'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)

        self.assertEquals(res.status_int, 200)

        res_dict = json.loads(res.body)

        self.assertEquals(0, res_dict['image']['min_ram'])

    def test_create_image_with_min_disk_default(self):
        """Tests that the /images POST registry API creates the image"""
        fixture = {'name': 'fake public image',
                   'is_public': True,
                   'disk_format': 'vhd',
                   'container_format': 'ovf'}

        req = webob.Request.blank('/images')

        req.method = 'POST'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)

        self.assertEquals(res.status_int, 200)

        res_dict = json.loads(res.body)

        self.assertEquals(0, res_dict['image']['min_disk'])

    def test_create_image_with_bad_status(self):
        """Tests proper exception is raised if a bad status is set"""
        fixture = {'id': _gen_uuid(),
                   'name': 'fake public image',
                   'is_public': True,
                   'disk_format': 'vhd',
                   'container_format': 'ovf',
                   'status': 'bad status'}

        req = webob.Request.blank('/images')

        req.method = 'POST'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)
        self.assertEquals(res.status_int, webob.exc.HTTPBadRequest.code)
        self.assertTrue('Invalid image status' in res.body)

    def test_create_image_with_bad_id(self):
        """Tests proper exception is raised if a bad disk_format is set"""
        fixture = {'id': 'asdf',
                   'name': 'fake public image',
                   'is_public': True,
                   'disk_format': 'vhd',
                   'container_format': 'ovf'}

        req = webob.Request.blank('/images')

        req.method = 'POST'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)
        self.assertEquals(res.status_int, webob.exc.HTTPBadRequest.code)

    def test_update_image(self):
        """Tests that the /images PUT registry API updates the image"""
        fixture = {'name': 'fake public image #2',
                   'min_disk': 5,
                   'min_ram': 256,
                   'disk_format': 'raw'}

        req = webob.Request.blank('/images/%s' % UUID2)

        req.method = 'PUT'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)

        self.assertEquals(res.status_int, 200)

        res_dict = json.loads(res.body)

        self.assertNotEquals(res_dict['image']['created_at'],
                             res_dict['image']['updated_at'])

        for k, v in fixture.iteritems():
            self.assertEquals(v, res_dict['image'][k])

    def test_update_image_not_existing(self):
        """
        Tests proper exception is raised if attempt to update
        non-existing image
        """
        fixture = {'status': 'killed'}

        req = webob.Request.blank('/images/%s' % _gen_uuid())

        req.method = 'PUT'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)
        self.assertEquals(res.status_int,
                          webob.exc.HTTPNotFound.code)

    def test_update_image_with_bad_status(self):
        """Tests that exception raised trying to set a bad status"""
        fixture = {'status': 'invalid'}

        req = webob.Request.blank('/images/%s' % UUID2)

        req.method = 'PUT'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image=fixture))

        res = req.get_response(self.api)
        self.assertEquals(res.status_int, webob.exc.HTTPBadRequest.code)
        self.assertTrue('Invalid image status' in res.body)

    def test_delete_image(self):
        """Tests that the /images DELETE registry API deletes the image"""

        # Grab the original number of images
        req = webob.Request.blank('/images')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        orig_num_images = len(res_dict['images'])

        # Delete image #2
        req = webob.Request.blank('/images/%s' % UUID2)
        req.method = 'DELETE'

        res = req.get_response(self.api)

        self.assertEquals(res.status_int, 200)

        # Verify one less image
        req = webob.Request.blank('/images')
        res = req.get_response(self.api)
        res_dict = json.loads(res.body)
        self.assertEquals(res.status_int, 200)

        new_num_images = len(res_dict['images'])
        self.assertEquals(new_num_images, orig_num_images - 1)

    def test_delete_image_response(self):
        """Tests that the registry API delete returns the image metadata"""

        image = self.FIXTURES[0]
        req = webob.Request.blank('/images/%s' % image['id'])
        req.method = 'DELETE'
        res = req.get_response(self.api)

        self.assertEquals(res.status_int, 200)
        deleted_image = json.loads(res.body)['image']

        self.assertEquals(image['id'], deleted_image['id'])
        self.assertTrue(deleted_image['deleted'])
        self.assertTrue(deleted_image['deleted_at'])

    def test_delete_image_not_existing(self):
        """
        Tests proper exception is raised if attempt to delete
        non-existing image
        """
        req = webob.Request.blank('/images/%s' % _gen_uuid())
        req.method = 'DELETE'

        res = req.get_response(self.api)
        self.assertEquals(res.status_int,
                          webob.exc.HTTPNotFound.code)

    def test_get_image_members(self):
        """
        Tests members listing for existing images
        """
        req = webob.Request.blank('/images/%s/members' % UUID2)
        req.method = 'GET'

        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)

        memb_list = json.loads(res.body)
        num_members = len(memb_list['members'])
        self.assertEquals(num_members, 0)

    def test_get_image_members_not_existing(self):
        """
        Tests proper exception is raised if attempt to get members of
        non-existing image
        """
        req = webob.Request.blank('/images/%s/members' % _gen_uuid())
        req.method = 'GET'

        res = req.get_response(self.api)
        self.assertEquals(res.status_int,
                          webob.exc.HTTPNotFound.code)

    def test_get_member_images(self):
        """
        Tests image listing for members
        """
        req = webob.Request.blank('/shared-images/pattieblack')
        req.method = 'GET'

        res = req.get_response(self.api)
        self.assertEquals(res.status_int, 200)

        memb_list = json.loads(res.body)
        num_members = len(memb_list['shared_images'])
        self.assertEquals(num_members, 0)

    def test_replace_members(self):
        """
        Tests replacing image members raises right exception
        """
        self.api = test_utils.FakeAuthMiddleware(rserver.API(self.mapper),
                                                 is_admin=False)
        fixture = dict(member_id='pattieblack')

        req = webob.Request.blank('/images/%s/members' % UUID2)
        req.method = 'PUT'
        req.content_type = 'application/json'
        req.body = json.dumps(dict(image_memberships=fixture))

        res = req.get_response(self.api)
        self.assertEquals(res.status_int, webob.exc.HTTPUnauthorized.code)

    def test_add_member(self):
        """
        Tests adding image members raises right exception
        """
        self.api = test_utils.FakeAuthMiddleware(rserver.API(self.mapper),
                                                 is_admin=False)
        req = webob.Request.blank('/images/%s/members/pattieblack' % UUID2)
        req.method = 'PUT'

        res = req.get_response(self.api)
        self.assertEquals(res.status_int, webob.exc.HTTPUnauthorized.code)

    def test_delete_member(self):
        """
        Tests deleting image members raises right exception
        """
        self.api = test_utils.FakeAuthMiddleware(rserver.API(self.mapper),
                                                 is_admin=False)
        req = webob.Request.blank('/images/%s/members/pattieblack' % UUID2)
        req.method = 'DELETE'

        res = req.get_response(self.api)
        self.assertEquals(res.status_int, webob.exc.HTTPUnauthorized.code)

    def test_delete_member_invalid(self):
        """
        Tests deleting a invalid/non existing member raises right exception
        """
        self.api = test_utils.FakeAuthMiddleware(rserver.API(self.mapper),
                                                 is_admin=True)
        req = webob.Request.blank('/images/%s/members/pattieblack' % UUID2)
        req.method = 'DELETE'

        res = req.get_response(self.api)
        self.assertEquals(res.status_int, webob.exc.HTTPNotFound.code)
        self.assertTrue('Membership could not be found' in res.body)
