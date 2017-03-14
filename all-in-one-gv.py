#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import datetime
import hashlib
import hmac
import itertools
import json
import logging
import os
import pytz
import random
import requests
import requests.auth
import requests.exceptions
import sys
import urllib
import urlparse
from collections import Mapping, Iterable

# Set to True to fetch Global Variables for each server.
# This has a non-negligible performance impact on large inventories
FETCH_GV = True

# The IP registered in Ansible, you can set this to 'privateIp' if Ansible
# can access all your servers by their private IP.
IP_VARIABLE = 'publicIp'

# Allowed server status. A list of allowed status, [''] for any status
# A server needs an IP to be registered in Ansible, so suspended servers will
# generally not show up in the inventory even if you add 'suspended' in there
SERVER_STATUS = ['running', 'pending_terminate']


class ScalrApiClient(object):
    def __init__(self, api_url, key_id, key_secret):
        self.api_url = api_url
        self.key_id = key_id
        self.key_secret = key_secret
        self.logger = logging.getLogger("api[{0}]".format(self.api_url))
        self.logger.addHandler(logging.StreamHandler())
        self.session = ScalrApiSession(self)

    def list(self, path, **kwargs):
        data = []
        ident = False
        while path is not None:
            if ident:
                print
            body = self.session.get(path, **kwargs).json()
            data.extend(body["data"])
            path = body["pagination"]["next"]
            ident = True
        return data

    def create(self, *args, **kwargs):
        self._fuzz_ids(kwargs.get("json", {}))
        return self.session.post(*args, **kwargs).json().get("data")

    def fetch(self, *args, **kwargs):
        return self.session.get(*args, **kwargs).json()["data"]

    def delete(self, *args, **kwargs):
        self.session.delete(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self.session.post(*args, **kwargs).json()["data"]


class ScalrApiSession(requests.Session):
    def __init__(self, client):
        self.client = client
        super(ScalrApiSession, self).__init__()

    def prepare_request(self, request):
        if not request.url.startswith(self.client.api_url):
            request.url = "".join([self.client.api_url, request.url])
        request = super(ScalrApiSession, self).prepare_request(request)

        now = datetime.datetime.now(tz=pytz.timezone(os.environ.get("TZ", "UTC")))
        date_header = now.isoformat()

        url = urlparse.urlparse(request.url)

        # TODO - Spec isn't clear on whether the sorting should happen prior or after encoding
        if url.query:
            pairs = urlparse.parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
            pairs = [map(urllib.quote, pair) for pair in pairs]
            pairs.sort(key=lambda pair: pair[0])
            canon_qs = "&".join("=".join(pair) for pair in pairs)
        else:
            canon_qs = ""

        # Authorize
        sts = "\n".join([
            request.method,
            date_header,
            url.path,
            canon_qs,
            request.body if request.body is not None else ""
        ])

        sig = " ".join([
            "V1-HMAC-SHA256",
            base64.b64encode(hmac.new(str(self.client.key_secret), sts, hashlib.sha256).digest())
        ])

        request.headers.update({
            "X-Scalr-Key-Id": self.client.key_id,
            "X-Scalr-Signature": sig,
            "X-Scalr-Date": date_header,
            "X-Scalr-Debug": "1"
        })

        self.client.logger.debug("URL: %s", request.url)
        self.client.logger.debug("StringToSign: %s", repr(sts))
        self.client.logger.debug("Signature: %s", repr(sig))

        return request

    def request(self, *args, **kwargs):
        res = super(ScalrApiSession, self).request(*args, **kwargs)
        self.client.logger.info("%s - %s", " ".join(args), res.status_code)
        try:
            errors = res.json().get("errors", None)
            if errors is not None:
                for error in errors:
                    self.client.logger.warning("API Error (%s): %s", error["code"], error["message"])
        except ValueError:
            self.client.logger.error("Received non-JSON response from API!")
        res.raise_for_status()
        self.client.logger.debug("Received response: %s", res.text)
        return res

def get_env_servers(client, envId):
    if '' in SERVER_STATUS:
        servers_path = '/api/v1beta0/user/{envId}/servers/'.format(envId=envId)
        servers = client.list(servers_path)
    else:
        servers = list(itertools.chain.from_iterable([
            client.list('/api/v1beta0/user/{envId}/servers/?status={s}'.format(envId=envId, s=s))
            for s in SERVER_STATUS
        ]))

    global_variables = {}
    if FETCH_GV:
        for server in servers:
            sId = server['id']
            GV_path = '/api/v1beta0/user/{envId}/servers/{serverId}/global-variables/'.format(envId=envId, serverId=sId)
            global_variables[sId] = client.list(GV_path)

    farmIds = []
    farmRoleIds = []
    for s in servers:
        farmIds.append(s['farm']['id'])
        farmRoleIds.append(s['farmRole']['id'])
    farmIds = set(farmIds)
    farmRoleIds = set(farmRoleIds)

    farms = {}
    farm_path = '/api/v1beta0/user/{envId}/farms/{farmId}/'
    for farmId in farmIds:
        path = farm_path.format(envId=envId, farmId=farmId)
        farms[farmId] = client.fetch(path)

    farmRoles = {}
    farmRole_path = '/api/v1beta0/user/{envId}/farm-roles/{farmRoleId}/'
    for farmRoleId in farmRoleIds:
        path = farmRole_path.format(envId=envId, farmRoleId=farmRoleId)
        farmRoles[farmRoleId] = client.fetch(path)

    result = {'_meta' : 
                {'hostvars': {}}
             }
    for farmId, farm in farms.iteritems():
        result[farm['name']] = {'vars': {
                                        'id': farmId,
                                        'project': farm['project']['id'],
                                        'owner': farm['owner']['id']
                                    }, 
                                'children': []}
        for farmRoleId, farmRole in farmRoles.iteritems():
            if farmRole['farm']['id'] != farmId:
                continue
            farmRoleGroupId = 'farm-role-' + str(farmRoleId) + '-' + farmRole['alias']
            result[farm['name']]['children'].append(farmRoleGroupId)
            result[farmRoleGroupId] = {'hosts': [], 'vars': {
                                        'id': farmRoleId,
                                        'platform': farmRole['cloudPlatform'],
                                        'roleId': farmRole['role']['id']
                                      }}
            for server in servers:
                if server['farmRole']['id'] != farmRoleId:
                    continue
                if len(server[IP_VARIABLE]) == 0:
                    # Server has no public IP
                    continue
                result[farmRoleGroupId]['hosts'].append(server[IP_VARIABLE][0])
                result['_meta']['hostvars'][server[IP_VARIABLE][0]] = {
                    'SCALR_HOSTNAME': server['hostname'],
                    'SCALR_ID': server['id'],
                    'SCALR_INDEX': server['index'],
                    'SCALR_PUBLIC_IP': server['publicIp'],
                    'SCALR_PRIVATE_IP': server['privateIp'],
                    'SCALR_LAUNCHED': server['launched'],
                    'SCALR_LAUNCH_REASON': server['launchReason']
                }
                if FETCH_GV:
                    for gv in global_variables[server['id']]:
                        if not gv['name'].startswith('SCALR_') and 'computedValue' in gv:
                            result['_meta']['hostvars'][server[IP_VARIABLE][0]][gv['name']] = gv['computedValue']
    print json.dumps(result, indent=2)

def get_farm_servers(client, envId, farmId):
    if '' in SERVER_STATUS:
        servers_path = '/api/v1beta0/user/{envId}/farms/{farmId}/servers/'.format(envId=envId, farmId=farmId)
        servers = client.list(servers_path)
    else:
        servers = list(itertools.chain.from_iterable([
            client.list('/api/v1beta0/user/{envId}/farms/{farmId}/servers/?status={s}'.format(envId=envId, farmId=farmId, s=s))
            for s in SERVER_STATUS
        ]))

    global_variables = {}
    if FETCH_GV:
        for server in servers:
            sId = server['id']
            GV_path = '/api/v1beta0/user/{envId}/servers/{serverId}/global-variables/'.format(envId=envId, serverId=sId)
            global_variables[sId] = client.list(GV_path)

    farmRoleIds = []
    for s in servers:
        farmRoleIds.append(s['farmRole']['id'])
    farmRoleIds = set(farmRoleIds)

    farm_path = '/api/v1beta0/user/{envId}/farms/{farmId}/'.format(envId=envId, farmId=farmId)
    farm = client.fetch(farm_path)

    farmRoles = {}
    farmRole_path = '/api/v1beta0/user/{envId}/farm-roles/{farmRoleId}/'
    for farmRoleId in farmRoleIds:
        path = farmRole_path.format(envId=envId, farmRoleId=farmRoleId)
        farmRoles[farmRoleId] = client.fetch(path)

    result = {'_meta' : 
                {'hostvars': {}}
             }

    for farmRoleId, farmRole in farmRoles.iteritems():
        farmRoleGroupId = 'farm-role-' + str(farmRoleId) + '-' + farmRole['alias']
        result[farmRoleGroupId] = {'hosts': [], 'vars': {
                                    'id': farmRoleId,
                                    'platform': farmRole['cloudPlatform'],
                                    'roleId': farmRole['role']['id']
                                  }}
        for server in servers:
            if server['farmRole']['id'] != farmRoleId:
                continue
            if len(server[IP_VARIABLE]) == 0:
                # Server has no public IP
                continue
            result[farmRoleGroupId]['hosts'].append(server[IP_VARIABLE][0])
            result['_meta']['hostvars'][server[IP_VARIABLE][0]] = {
                'SCALR_HOSTNAME': server['hostname'],
                'SCALR_ID': server['id'],
                'SCALR_INDEX': server['index'],
                'SCALR_PUBLIC_IP': server['publicIp'],
                'SCALR_PRIVATE_IP': server['privateIp'],
                'SCALR_LAUNCHED': server['launched'],
                'SCALR_LAUNCH_REASON': server['launchReason']
            }
            if FETCH_GV:
                for gv in global_variables[server['id']]:
                    if not gv['name'].startswith('SCALR_') and 'computedValue' in gv:
                        result['_meta']['hostvars'][server[IP_VARIABLE][0]][gv['name']] = gv['computedValue']
    print json.dumps(result, indent=2)

def get_acct_servers(client):
    env_path = '/api/v1beta0/account/environments/'
    envs = client.list(env_path)
    result = {'_meta' : 
                {'hostvars': {}}
             }
    for e in envs:
        envId = e['id']

        if '' in SERVER_STATUS:
            servers_path = '/api/v1beta0/user/{envId}/servers/'.format(envId=envId)
            servers = client.list(servers_path)
        else:
            servers = list(itertools.chain.from_iterable([
                client.list('/api/v1beta0/user/{envId}/servers/?status={s}'.format(envId=envId, s=s))
                for s in SERVER_STATUS
            ]))

        global_variables = {}
        if FETCH_GV:
            for server in servers:
                sId = server['id']
                GV_path = '/api/v1beta0/user/{envId}/servers/{serverId}/global-variables/'.format(envId=envId, serverId=sId)
                global_variables[sId] = client.list(GV_path)

        farms_path = '/api/v1beta0/user/{envId}/farms/'.format(envId=envId)
        farms = client.list(farms_path)

        started_farms = set([s['farm']['id'] for s in servers])
        farms = {f['id']: f for f in farms if f['id'] in started_farms}

        farmRoles_path = '/api/v1beta0/user/{envId}/farms/{farmId}/farm-roles/'
        farmRoles = {}
        for farmId in started_farms:
            farmRoles.update({f['id']: f for f in client.list(farmRoles_path.format(envId=envId, farmId=farmId))})

        envGroups = {}
        for s in servers:
            if len(s[IP_VARIABLE]) == 0:
                # Can't find an IP
                continue
            serverFarm = s['farm']['id']
            serverFarmRole = s['farmRole']['id']
            if not serverFarm in envGroups:
                farm = farms[serverFarm]
                envGroups[serverFarm] =  {'vars': {
                                            'id': serverFarm,
                                            'project': farm['project']['id'],
                                            'owner': farm['owner']['id']
                                        }, 
                                        'children': {}}
            farmGroup = envGroups[serverFarm]
            if not serverFarmRole in farmGroup['children']:
                farmRole = farmRoles[serverFarmRole]
                farmGroup['children'][serverFarmRole] = {'hosts': [], 'vars': {
                                                        'id': serverFarmRole,
                                                        'platform': farmRole['cloudPlatform'],
                                                        'roleId': farmRole['role']['id']
                                                      }}
            farmRoleGroup = farmGroup['children'][serverFarmRole]
            farmRoleGroup['hosts'].append(s[IP_VARIABLE][0])
            result['_meta']['hostvars'][s[IP_VARIABLE][0]] = {
                'SCALR_HOSTNAME': s['hostname'],
                'SCALR_ID': s['id'],
                'SCALR_INDEX': s['index'],
                'SCALR_PUBLIC_IP': s['publicIp'],
                'SCALR_PRIVATE_IP': s['privateIp'],
                'SCALR_LAUNCHED': s['launched'],
                'SCALR_LAUNCH_REASON': s['launchReason']
            }
            if FETCH_GV:
                for gv in global_variables[s['id']]:
                    if not gv['name'].startswith('SCALR_') and 'computedValue' in gv:
                        result['_meta']['hostvars'][s[IP_VARIABLE][0]][gv['name']] = gv['computedValue']

        # Unpacking, 1: farm roles
        for farmId, farmGroup in envGroups.items():
            farmRoleGroups = []
            for farmRoleId, farmRoleGroup in farmGroup['children'].items():
                farmRoleGroupId = 'farm-role-' + str(farmRoleId) + '-' + farmRoles[farmRoleId]['alias']
                result[farmRoleGroupId] = farmRoleGroup
                farmRoleGroups.append(farmRoleGroupId)
            farmGroup['children'] = farmRoleGroups

        # Unpacking, 2: farms
        farmGroups = []
        for farmId, farmGroup in envGroups.items():
            farmGroupId = 'farm-' + str(farmId) + '-' + farms[farmId]['name']
            result[farmGroupId] = farmGroup
            farmGroups.append(farmGroupId)

        envRepr = {
            'vars': {
                'status': e['status']
            },
            'children': farmGroups
        }
        result['Env ' + str(envId) + ': ' + e['name']] = envRepr

    print json.dumps(result, indent=2)


def main():
    api_url = os.environ.get('SCALR_API_URL')
    api_key_id = os.environ.get('SCALR_API_KEY_ID')
    api_key_secret = os.environ.get('SCALR_API_KEY_SECRET')
    env_id = os.environ.get('SCALR_ENV_ID')
    farm_id = os.environ.get('SCALR_FARM_ID')

    if not api_url:
        print 'API URL not specified, exiting.'
        return
    if not api_key_id:
        print 'API Key ID not specified, exiting.'
        return
    if not api_key_secret:
        print 'API Key Secret not specified, exiting.'
        return

    client = ScalrApiClient(api_url.rstrip("/"), api_key_id, api_key_secret)
    if env_id:
        if farm_id:
            get_farm_servers(client, env_id, farm_id)
        else:
            get_env_servers(client, env_id)
    else:
        get_acct_servers(client)

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == '--list':
        main()
    else:
        print '{}'

