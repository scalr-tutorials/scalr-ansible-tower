#!/usr/bin/env python 

import json
import requests.exceptions

import requests
import requests.auth

from api.client import ScalrApiClient

def get_env_servers(client, envId):
    servers_path = '/api/v1beta0/user/{envId}/servers/?status=running'.format(envId=envId)
    servers = client.list(servers_path)

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
                                        'platform': farmRole['platform'],
                                        'roleId': farmRole['role']['id']
                                      }}
            for server in servers:
                if server['farmRole']['id'] != farmRoleId:
                    continue
                if len(server['publicIp']) == 0:
                    # Server has no public IP
                    continue
                result[farmRoleGroupId]['hosts'].append(server['publicIp'][0])
                result['_meta']['hostvars'][server['publicIp'][0]] = {'hostname': server['hostname']}
    print json.dumps(result, indent=2)

def get_farm_servers(client, envId, farmId):
    servers_path = '/api/v1beta0/user/{envId}/farms/{farmId}/servers/?status=running'.format(envId=envId, farmId=farmId)
    servers = client.list(servers_path)

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
                                    'platform': farmRole['platform'],
                                    'roleId': farmRole['role']['id']
                                  }}
        for server in servers:
            if server['farmRole']['id'] != farmRoleId:
                continue
            if len(server['publicIp']) == 0:
                # Server has no public IP
                continue
            result[farmRoleGroupId]['hosts'].append(server['publicIp'][0])
            result['_meta']['hostvars'][server['publicIp'][0]] = {'hostname': server['hostname']}
    print json.dumps(result, indent=2)


def main(api_url, api_key_id, api_key_secret, env_id, farm_id):
    client = ScalrApiClient(api_url.rstrip("/"), api_key_id, api_key_secret)
    if farm_id:
        get_farm_servers(client, env_id, farm_id)
    else:
        get_env_servers(client, env_id)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("api_url", help="URL of Scalr. For instance: https://my.scalr.com")
    parser.add_argument("key_id", help="Your Scalr API Key ID")
    parser.add_argument("key_secret", help="Your Scalr API Key Secret")
    parser.add_argument("env_id", help="The ID of the environment to use")
    parser.add_argument("farm_id", nargs='?', default=None, help="Optional: get only the servers that belong to this Farm")

    ns = parser.parse_args()

    main(ns.api_url, ns.key_id, ns.key_secret, ns.env_id, ns.farm_id)
