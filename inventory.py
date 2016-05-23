#!/usr/bin/env python 

import json
import requests.exceptions

import requests
import requests.auth

from api.client import ScalrApiClient

def get_servers(client, envId):
    servers_path = '/api/v1beta0/user/{envId}/servers/'.format(envId=envId)
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
            farmRoleGroupId = str(farmRoleId) + '-' + farmRole['alias']
            result[farm['name']]['children'].append(farmRoleGroupId)
            result[farmRoleGroupId] = {'hosts': [], 'vars': {
                                        'id': farmRoleId,
                                        'platform': farmRole['platform'],
                                        'roleId': farmRole['role']['id']
                                      }}
            for server in servers:
                if server['farmRole']['id'] != farmRoleId:
                    continue
                result[farmRoleGroupId]['hosts'].append(server['publicIp'][0])
                result['_meta']['hostvars'][server['publicIp'][0]] = {'hostname': server['hostname']}
    print json.dumps(result, indent=2)



def main(credentials_file):
    # Setup credentials
    with open(credentials_file) as f:
        creds = json.load(f)
        api_url, api_key_id, api_key_secret, env_id, basic_auth_username, basic_auth_password = \
                [creds.get(k, "") for k in ["api_url", "api_key_id", "api_key_secret", "env_id", "basic_auth_username", "basic_auth_password"]]

    client = ScalrApiClient(api_url.rstrip("/"), api_key_id, api_key_secret)
    client.session.auth = requests.auth.HTTPBasicAuth(basic_auth_username, basic_auth_password)

    get_servers(client, env_id)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("credentials", help="Path to credentials file")

    ns = parser.parse_args()

    main(ns.credentials)
