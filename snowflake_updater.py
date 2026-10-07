import snowflake.connector
import warnings
import configparser
import argparse
import getpass
import os
import sys
import csv
import json
import re
import html
import datetime
import requests
from cryptography.fernet import Fernet
warnings.filterwarnings("ignore")

# Resolve paths relative to this script's directory so double-clicking works
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def strip_html(text):
    """Remove HTML tags and decode entities from a description string."""
    text = re.sub(r'<[^>]+>', '', text)   # strip tags
    text = html.unescape(text)             # &amp; -> &, &nbsp; -> space, etc.
    text = re.sub(r'\s+', ' ', text).strip()
    return text

version = 20231202

# ===========================================================================
# Encryption helpers (formerly my_encrypt.py)
# ===========================================================================

def generate_key():
    key = Fernet.generate_key()
    with open(os.path.join(SCRIPT_DIR, "secret.key"), "wb") as key_file:
        key_file.write(key)

def load_key():
    return open(os.path.join(SCRIPT_DIR, "secret.key"), "rb").read()

def encrypt_message(message):
    f = Fernet(load_key())
    return f.encrypt(message.encode()).decode()

def decrypt_message(encrypted_message):
    f = Fernet(load_key())
    return f.decrypt(encrypted_message.encode()).decode()


# ===========================================================================
# CDGC API (formerly idmc_api.py)
# ===========================================================================

cdgc_debug = False


class INFA_DG_Object:

    def debug(self, message):
        if cdgc_debug:
            print(f"DEBUG: {message}")

    def getvalue(self, key):
        return self.map[key]

    def fetchOtherRelationships(self):

        for payload in [
            json.dumps({
                "from": 0, "size": 10000,
                "query": {"bool": {"filter": [
                    {"term": {"elementType": "RELATIONSHIP"}},
                    {"term": {"type": "com.infa.ccgf.models.governance.IClassTechnicalGlossaryBase"}}
                ]}},
                "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
            }),
            json.dumps({
                "from": 0, "size": 10000,
                "query": {"bool": {"filter": [
                    {"term": {"elementType": "RELATIONSHIP"}},
                    {"term": {"type": "core.ClassifiedAs"}}
                ]}},
                "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
            }),
        ]:
            response_data = self.session.DG_elastic_search(payload)
            for search_obj in response_data['hits']['hits']:
                try:
                    raw_map = search_obj['sourceAsMap']
                    if (search_obj['sourceAsMap']['elementType'] == 'RELATIONSHIP'
                            and 'ACCEPTED' in raw_map['core.curationStatus']
                            and (self.origin == raw_map['core.sourceOrigin']
                                 or self.origin == raw_map['core.targetOrigin'])):
                        source_obj = self.session.getObjectByID(raw_map['core.sourceIdentity'])
                        target_obj = self.session.getObjectByID(raw_map['core.targetIdentity'])
                        if target_obj.classType in ('core.DataElementClassification', 'core.DataEntityClassification'):
                            if target_obj not in source_obj.classifications:
                                source_obj.classifications.append(target_obj)
                        elif target_obj.classType == 'com.infa.ccgf.models.governance.BusinessTerm':
                            if target_obj not in source_obj.businessterms:
                                source_obj.businessterms.append(target_obj)
                except:
                    pass

    def fetchObjects(self):
        PAGE_SIZE = 1000
        offset = 0
        all_hits = []

        print(f"INFO: Fetching Detailed Information for {self.name}")
        while True:
            payload = json.dumps({
                "from": offset,
                "size": PAGE_SIZE,
                "query": {"term": {"core.origin": self.origin}},
                "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
            })

            response_data = self.session.DG_elastic_search(payload)
            hits = response_data['hits']['hits']
            all_hits.extend(hits)

            total = response_data['hits']['total']
            if isinstance(total, dict):
                total = total['value']

            print(f"INFO:   Fetched {len(all_hits)} / {total} objects...")
            if len(all_hits) >= total or len(hits) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        for obj in all_hits:
            try:
                if obj['sourceAsMap']['elementType'] == 'OBJECT':
                    raw_map = obj['sourceAsMap']
                    o = INFA_DG_Object(self.session, raw_map)
                    self.objects.append(o)
                    self.session.all_objects.append(o)
                elif obj['sourceAsMap']['elementType'] == 'RELATIONSHIP':
                    self.raw_relationships.append(obj['sourceAsMap'])
            except:
                pass

        for obj in all_hits:
            try:
                if obj['sourceAsMap']['elementType'] == 'RELATIONSHIP':
                    raw_map = obj['sourceAsMap']
                    source_id = raw_map['core.sourceIdentity']
                    target_id = raw_map['core.targetIdentity']

                    target_obj = None
                    source_obj = None
                    for o in self.session.all_objects:
                        if o.identity == source_id:
                            source_obj = o
                        if o.identity == target_id:
                            target_obj = o

                    try:
                        if obj['sourceAsMap']['core.associationKind'] == "core.ParentChild":
                            if source_obj is not None and target_obj is not None:
                                source_obj.child_objects.append(target_obj)
                                target_obj.parent_objects.append(source_obj)
                    except:
                        pass

                    if ('ACCEPTED' in raw_map['core.curationStatus']
                            and target_obj.classType in ('core.DataElementClassification', 'core.DataEntityClassification')):
                        source_obj.classifications.append(target_obj)
                    elif ('ACCEPTED' in raw_map['core.curationStatus']
                          and target_obj.classType == 'com.infa.ccgf.models.governance.BusinessTerm'):
                        source_obj.businessterms.append(target_obj)
            except:
                pass

        self.fetchOtherRelationships()

    def getObjectsByShortType(self, shortType):
        return [i for i in self.objects if i.shortType.lower() == shortType.lower()]

    def getObjectsByType(self, classType):
        return [i for i in self.objects if i.classType == classType]

    def getClassificationNames(self):
        return ','.join(o.name for o in self.classifications)

    def getBusinessTermNames(self):
        return ','.join(o.name for o in self.businessterms)

    def getFriendlyId(self):
        try:
            originFriendlyName = self.session.getObjectByLocationID(self.origin).name
            return self.externalId.split('~')[0].replace(self.origin, originFriendlyName)
        except:
            print(f"Error getting friendly name session.getObjectByLocationID({self.origin}).name")

    def getAllRelatedPolicies(self):
        result_array = []
        for obj in self.parentPolicies:
            result_array.append(obj.name)
        for bt in self.businessterms:
            for p in bt.parentPolicies:
                result_array.append(p.name)
        for cl in self.classifications:
            for p in cl.parentPolicies:
                result_array.append(p.name)
        for obj in self.child_objects:
            result_array += obj.getAllRelatedPolicies()
        return list(set(result_array))

    def getParentPolicyNames(self):
        return ','.join(self.getAllRelatedPolicies())

    def __init__(self, session, raw_map):
        self.classifications = []
        self.businessterms = []
        self.objects = []
        self.raw_relationships = []
        self.parent_objects = []
        self.child_objects = []
        self.session = session
        self.name = raw_map['core.name']
        self.isResource = False
        self.isDataSet = False
        self.isDataElement = False
        try:
            for x in raw_map['type']:
                if x == "core.DataElement":
                    self.isDataElement = True
                if x == "core.DataSet":
                    self.isDataSet = True
        except:
            pass
        self.map = raw_map
        self.description = ""
        try:
            self.description = self.getvalue('core.description')
        except:
            pass
        self.origin = self.getvalue('core.origin')
        self.externalId = self.getvalue('core.externalId')
        self.classType = self.getvalue('core.classType')
        self.shortType = self.classType.split('.')[-1]
        self.elementType = self.getvalue('elementType')
        self.identity = self.getvalue('core.identity')
        self.parentPolicies = []


class INFASession:

    def debug(self, message):
        if cdgc_debug:
            print(f"DEBUG: {message}")

    def get_sessionid_and_orgid(self, username, password):
        url = self.url_base + '/identity-service/api/v1/Login'
        response = requests.request("POST", url,
                                    headers={'Content-Type': 'application/json'},
                                    data=json.dumps({'username': username, 'password': password}))
        response_data = response.json()
        return response_data['sessionId'], response_data['currentOrgId']

    def get_token(self, session_id, org_id):
        url = self.url_base + "/identity-service/api/v1/jwt/Token?client_id=cdlg_app&nonce=gxx3t69BWB49BHHNn&access_code="
        response = requests.request("GET", url,
                                    headers={'Content-Type': 'application/json',
                                             'IDS-SESSION-ID': session_id,
                                             'X-INFA-ORG-ID': org_id})
        return response.json()['jwt_token']

    def DG_elastic_search(self, json_query):
        url = self.hawk_url_base + "/ccgf-searchv2/api/v1/search"
        headers = {
            'Content-Type': 'application/json',
            'X-INFA-SEARCH-LANGUAGE': 'elasticsearch',
            'X-INFA-ORG-ID': self.org_id,
            'Authorization': 'Bearer ' + self.token
        }
        self.debug(f"DG_elastic_search: {url}  payload: {json_query}")
        response = requests.request("POST", url, headers=headers, data=json_query)
        self.debug(f"DG_elastic_search response: {response.text}")
        return response.json()

    def DG_publish(self, json_payload):
        url = self.hawk_url_base + "/ccgf-contentv2/api/v1/publish"
        headers = {
            'Content-Type': 'application/json',
            'X-INFA-SEARCH-LANGUAGE': 'elasticsearch',
            'X-INFA-ORG-ID': self.org_id,
            'Authorization': 'Bearer ' + self.token
        }
        self.debug("publish: " + json_payload)
        return requests.request("POST", url, headers=headers, data=json_payload).json()

    def deleteById(self, obj_identity):
        o = self.getObjectByID(obj_identity)
        payload = json.dumps({"items": [{"elementType": o.elementType, "identity": obj_identity,
                                          "operation": "DELETE", "type": o.classType,
                                          "identityType": "INTERNAL", "attributes": {}}]})
        return self.DG_publish(payload)

    def fetchResources(self):
        payload = json.dumps({
            "from": 0, "size": 1000,
            "query": {"term": {"core.classType": "core.Resource"}},
            "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
        })
        response_data = self.DG_elastic_search(payload)
        for obj in response_data['hits']['hits']:
            try:
                resource = INFA_DG_Object(self, obj['sourceAsMap'])
                resource.isResource = True
                self.resources.append(resource)
            except:
                pass

    def fetchClassifications(self):
        self.fetchParentPolicyOfClassifications()
        for classType in ('core.DataElementClassification', 'core.DataEntityClassification'):
            payload = json.dumps({
                "from": 0, "size": 1000,
                "query": {"term": {"core.classType": classType}},
                "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
            })
            response_data = self.DG_elastic_search(payload)
            for obj in response_data['hits']['hits']:
                try:
                    classification = INFA_DG_Object(self, obj['sourceAsMap'])
                    if classType == 'core.DataElementClassification':
                        classification.parentPolicies = self.fetchParentPolicyOfClassification(classification.identity)
                    self.classifications.append(classification)
                    self.all_objects.append(classification)
                except:
                    pass

    def fetchParentPolicyOfClassifications(self):
        payload = json.dumps({
            "from": 0, "size": 1000,
            "query": {"bool": {"filter": [
                {"term": {"elementType": "RELATIONSHIP"}},
                {"term": {"type": "com.infa.ccgf.models.governance.relatedPolicyClassification"}}
            ]}},
            "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
        })
        for res in self.DG_elastic_search(payload)['hits']['hits']:
            self.all_relationships.append({
                "name": res['sourceAsMap']['core.sourceIdentity'] + " " + res['sourceAsMap']['core.targetIdentity'],
                "source_identity": res['sourceAsMap']['core.sourceIdentity'],
                "target_identity": res['sourceAsMap']['core.targetIdentity']
            })

    def fetchParentPolicyOfClassification(self, classification_id):
        result = []
        for rel in self.all_relationships:
            if rel["target_identity"] == classification_id:
                for pol in self.policies:
                    if pol.identity == rel["source_identity"]:
                        self.debug(f"Adding policy {pol.name} as parent of Classification {classification_id}")
                        result.append(pol)
        return result

    def fetchParentPolicyOfBusinessTerms(self):
        payload = json.dumps({
            "from": 0, "size": 1000,
            "query": {"bool": {"filter": [
                {"term": {"elementType": "RELATIONSHIP"}},
                {"term": {"type": "com.infa.ccgf.models.governance.relatedBusinessTermPolicy"}}
            ]}},
            "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
        })
        for res in self.DG_elastic_search(payload)['hits']['hits']:
            self.all_relationships.append({
                "name": res['sourceAsMap']['core.sourceIdentity'] + " " + res['sourceAsMap']['core.targetIdentity'],
                "source_identity": res['sourceAsMap']['core.sourceIdentity'],
                "target_identity": res['sourceAsMap']['core.targetIdentity']
            })

    def fetchParentPolicyOfBusinessTerm(self, business_term_id):
        result = []
        for rel in self.all_relationships:
            if rel["target_identity"] == business_term_id:
                for pol in self.policies:
                    if pol.identity == rel["source_identity"]:
                        self.debug(f"Adding policy {pol.name} as parent of Business Term {business_term_id}")
                        result.append(pol)
        return result

    def fetchPolicies(self):
        payload = json.dumps({
            "from": 0, "size": 1000,
            "query": {"term": {"core.classType": "com.infa.ccgf.models.governance.Policy"}},
            "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
        })
        for obj in self.DG_elastic_search(payload)['hits']['hits']:
            try:
                pol = INFA_DG_Object(self, obj['sourceAsMap'])
                self.policies.append(pol)
                self.all_objects.append(pol)
            except:
                pass

    def fetchBusinessTerms(self):
        self.fetchParentPolicyOfBusinessTerms()
        payload = json.dumps({
            "from": 0, "size": 1000,
            "query": {"term": {"core.classType": "com.infa.ccgf.models.governance.BusinessTerm"}},
            "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
        })
        for obj in self.DG_elastic_search(payload)['hits']['hits']:
            try:
                term = INFA_DG_Object(self, obj['sourceAsMap'])
                term.parentPolicies = self.fetchParentPolicyOfBusinessTerm(term.identity)
                self.businessterms.append(term)
                self.all_objects.append(term)
            except:
                pass

    def getObjectByID(self, identity):
        for o in self.all_objects:
            if o.identity == identity:
                return o
        payload = json.dumps({
            "from": 0, "size": 1000,
            "query": {"bool": {"filter": [{"term": {"core.identity": identity}}]}},
            "sort": [{"com.infa.ccgf.models.governance.scannedTime": {"order": "desc"}}]
        })
        for obj in self.DG_elastic_search(payload)['hits']['hits']:
            try:
                o = INFA_DG_Object(self, obj['sourceAsMap'])
                self.all_objects.append(o)
                return o
            except:
                pass

    def getObjectByLocationID(self, locationID):
        for o in self.resources:
            if o.origin == locationID and o.isResource:
                return o

    def getObjectByName(self, name):
        for o in self.all_objects:
            if o.name == name:
                return o

    def __init__(self, username, password, url_base, hawk_url_base):
        self.all_relationships = []
        self.all_objects = []
        self.businessterms = []
        self.classifications = []
        self.policies = []
        self.resources = []
        self.url_base = url_base
        self.hawk_url_base = hawk_url_base
        self.session_id, self.org_id = self.get_sessionid_and_orgid(username, password)
        self.token = self.get_token(self.session_id, self.org_id)
        print(f"INFO: Fetching Policy Information")
        self.fetchPolicies()
        print(f"INFO: Fetching Resource Information")
        self.fetchResources()
        print(f"INFO: Fetching Classification Information")
        self.fetchClassifications()
        print(f"INFO: Fetching Business Term Information")
        self.fetchBusinessTerms()


# ===========================================================================
# Argument parsing
# ===========================================================================

parser = argparse.ArgumentParser(description='Snowflake Updater — writes CDGC metadata to Snowflake')
parser.add_argument('--config', default=os.path.join(SCRIPT_DIR, 'config.ini'),
                    help='Path to config file (default: config.ini in script directory)')
parser.add_argument('--update-config', action='store_true',
                    help='Interactively update the config file, then exit')
args = parser.parse_args()


# ===========================================================================
# Config update mode
# ===========================================================================

def update_config(config_path):
    """Interactively walk through every config value and prompt for updates.
    Passwords are entered as plaintext and stored encrypted; the plain
    'password' field is always cleared when an encrypted value is set.
    """
    if not os.path.exists(os.path.join(SCRIPT_DIR, 'secret.key')):
        print("INFO: No secret.key found — generating a new encryption key.")
        generate_key()
        print("INFO: secret.key created. Keep this file safe; it is required to decrypt passwords.\n")

    config = configparser.ConfigParser()
    config.read(config_path)

    if not config.sections():
        print(f"ERROR: Config file '{config_path}' not found or is empty.")
        return

    print(f"Updating: {config_path}")
    print("Leave any prompt blank to keep the current value.\n")

    POD_HINT = ("  (Derives url_base  -> https://<pod>.informaticacloud.com\n"
                "           hawk_url  -> https://cdgc-api.<pod>.informaticacloud.com)")

    updated = False

    for section in config.sections():
        print(f"[{section}]")
        for key in config[section]:
            current_val = config[section][key]

            if key == 'password':
                continue

            elif key == 'encrypted_password':
                status = "[currently set — encrypted]" if len(current_val) > 2 else "[not set]"
                print(f"  {key}: {status}")
                print(f"  NOTE: Enter a plaintext password — it will be encrypted and stored.")
                print(f"        Leave blank to keep the current value.")
                new_val = getpass.getpass(f"  Enter password: ")
                if new_val:
                    config[section][key] = encrypt_message(new_val)
                    config[section]['password'] = ''
                    updated = True
                    print(f"  Password encrypted and saved.")

            elif current_val.strip().lower() in ('true', 'false'):
                print(f"  {key}: {current_val}")
                raw = input(f"  Enter new value (True/False, leave blank to keep): ").strip()
                if raw:
                    if raw[0].upper() in ('T', 'Y', '1'):
                        config[section][key] = 'True'
                    elif raw[0].upper() in ('F', 'N', '0'):
                        config[section][key] = 'False'
                    updated = True

            elif key == 'pod':
                print(f"  {key}: {current_val}")
                print(POD_HINT)
                new_val = input(f"  Enter new value (leave blank to keep): ").strip()
                if new_val:
                    config[section][key] = new_val
                    updated = True

            else:
                print(f"  {key}: {current_val}")
                new_val = input(f"  Enter new value (leave blank to keep): ").strip()
                if new_val:
                    config[section][key] = new_val
                    updated = True

        print()

    if updated:
        with open(config_path, 'w') as f:
            config.write(f)
        print(f"INFO: Config saved to {config_path}")
    else:
        print("INFO: No changes made.")


if args.update_config:
    update_config(args.config)
    sys.exit(0)


# ===========================================================================
# Logging — tee all output to a timestamped log file
# ===========================================================================

class _Tee:
    def __init__(self, *streams):
        self._streams = streams
    def write(self, data):
        for s in self._streams:
            s.write(data)
    def flush(self):
        for s in self._streams:
            s.flush()
    def isatty(self):
        return False

_ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
_log_path = os.path.join(SCRIPT_DIR, f'run_snowflake_updater_{_ts}.log')
_log_file = open(_log_path, 'w', encoding='utf-8', buffering=1)
sys.stdout = _Tee(sys.__stdout__, _log_file)
print(f"INFO: Logging to {_log_path}")


# ===========================================================================
# Load config
# ===========================================================================

config = configparser.ConfigParser()
config.read(args.config)

# --- IDMC ---
catalog_user           = config['IDMC'].get('username')
catalog_pass           = config['IDMC'].get('password', '')
encrypted_catalog_pass = config['IDMC'].get('encrypted_password', '')
pod                    = config['IDMC'].get('pod', 'dmp-us')
url_base               = f'https://{pod}.informaticacloud.com'
hawk_url_base          = f'https://cdgc-api.{pod}.informaticacloud.com'
catalog_resource_name  = config['IDMC'].get('catalog_resource_name')
debugFlag              = config['IDMC'].get('debug', 'False').upper().startswith('T')

# --- TagWriteback ---
writeback_business_term      = config['TagWriteback'].get('writeback_business_term', 'True').upper().startswith('T')
writeback_business_term_tag  = config['TagWriteback'].get('writeback_business_term_tag')
writeback_parent_policy      = config['TagWriteback'].get('writeback_parent_policy', 'True').upper().startswith('T')
writeback_parent_policy_tag  = config['TagWriteback'].get('writeback_parent_policy_tag')
writeback_classification     = config['TagWriteback'].get('writeback_classification', 'True').upper().startswith('T')
writeback_classification_tag = config['TagWriteback'].get('writeback_classification_tag')
unset_tags_first             = config['TagWriteback'].get('unset_tags_first', 'True').upper().startswith('T')
stop_and_verify              = config['TagWriteback'].get('stop_and_verify', 'True').upper().startswith('T')
writeback_dq_score           = config['TagWriteback'].get('writeback_dq_score', 'False').upper().startswith('T')
writeback_dq_score_tag       = config['TagWriteback'].get('writeback_dq_score_tag')
writeback_description        = config['TagWriteback'].get('writeback_description', 'False').upper().startswith('T')
full_object_report           = config['TagWriteback'].get('full_object_report', 'False').upper().startswith('T')

# ---------------------------------------------------------------------------
# Developer debug options — edit here, not in config.ini
# ---------------------------------------------------------------------------
capture_raw_objects      = False
capture_raw_object_names = []  # e.g. ['NS_MKTG_USER', 'MKTG_EMAIL_ID']

# --- Snowflake ---
sf_account              = config['Snowflake'].get('account')
sf_database             = config['Snowflake'].get('database')
sf_username             = config['Snowflake'].get('username')
sf_password             = config['Snowflake'].get('password', '')
encrypted_sf_password   = config['Snowflake'].get('encrypted_password', '')
sf_role                 = config['Snowflake'].get('role')
sf_private_key_file     = config['Snowflake'].get('private_key_file', '').strip()
sf_private_key_pass     = config['Snowflake'].get('private_key_passphrase', '').strip()

cdgc_debug = debugFlag


def debug(message):
    if debugFlag:
        print(f"DEBUG: {message}")


# ===========================================================================
# Connect to CDGC and build statement lists
# ===========================================================================

print(f"INFO: Connecting to Catalog as user {catalog_user}, and fetching some basic information")
if len(encrypted_catalog_pass) > 2:
    catalog_pass = decrypt_message(encrypted_catalog_pass)
session = INFASession(username=catalog_user, password=catalog_pass, url_base=url_base, hawk_url_base=hawk_url_base)

statements = []
unset_statements = []
unset_tags = []

if unset_tags_first:
    if writeback_business_term:
        unset_tags.append(writeback_business_term_tag)
    if writeback_parent_policy:
        unset_tags.append(writeback_parent_policy_tag)
    if writeback_classification:
        unset_tags.append(writeback_classification_tag)
    if writeback_dq_score:
        unset_tags.append(writeback_dq_score_tag)

for r in session.resources:
    if r.name == catalog_resource_name:
        r.fetchObjects()
        print(f"INFO: Evaluating {catalog_resource_name}")

        # Build DQ score lookup: asset identity -> averaged score across rule types
        dq_scores = {}
        if writeback_dq_score:
            raw_scores = {}
            for _o in r.objects:
                if _o.shortType == 'DQResult' and '~' in _o.name:
                    asset_id = _o.name.split('~')[1]
                    score = _o.map.get('core.score')
                    if score is not None:
                        raw_scores.setdefault(asset_id, []).append(score)
            dq_scores = {k: round(sum(v) / len(v), 2) for k, v in raw_scores.items()}
            print(f"INFO: DQ scores calculated for {len(dq_scores)} assets")

        if capture_raw_objects and capture_raw_object_names:
            print(f"INFO: Capturing raw objects for: {capture_raw_object_names}")
            captured_objects = []
            for _o in r.objects:
                if _o.name in capture_raw_object_names:
                    _fname = os.path.join(SCRIPT_DIR, f"debug/raw_{_o.shortType}_{_o.name}.json")
                    with open(_fname, 'w', encoding='utf-8') as _f:
                        json.dump(_o.map, _f, indent=2, default=str)
                    print(f"INFO:   Captured [{_o.shortType}] {_o.name} -> {_fname}")
                    captured_objects.append(_o)
            if not captured_objects:
                print(f"INFO:   No objects matched the capture list — check the names are exact.")
            else:
                captured_ids = {_o.identity for _o in captured_objects}
                matching_rels = [
                    _r for _r in r.raw_relationships
                    if _r.get('core.sourceIdentity') in captured_ids
                    or _r.get('core.targetIdentity') in captured_ids
                ]
                _fname = os.path.join(SCRIPT_DIR, "debug/raw_relationships_for_captured_objects.json")
                with open(_fname, 'w', encoding='utf-8') as _f:
                    json.dump(matching_rels, _f, indent=2, default=str)
                print(f"INFO:   Captured {len(matching_rels)} relationships -> {_fname}")

                linked_ids = set()
                for _r in matching_rels:
                    linked_ids.add(_r.get('core.sourceIdentity'))
                    linked_ids.add(_r.get('core.targetIdentity'))
                linked_ids -= captured_ids

                all_objects_by_id = {_o.identity: _o for _o in r.objects}
                linked_found = [all_objects_by_id[_id] for _id in linked_ids if _id in all_objects_by_id]

                if linked_found:
                    _by_type = {}
                    for _o in linked_found:
                        _by_type.setdefault(_o.shortType, []).append(_o.map)
                    for _type, _maps in _by_type.items():
                        _fname = os.path.join(SCRIPT_DIR, f"debug/raw_linked_{_type}_objects.json")
                        with open(_fname, 'w', encoding='utf-8') as _f:
                            json.dump(_maps, _f, indent=2, default=str)
                        print(f"INFO:   Captured {len(_maps)} linked [{_type}] objects -> {_fname}")
                else:
                    print(f"INFO:   No linked objects found in fetched set — they may need a separate query.")

        if full_object_report:
            report_file = os.path.join(SCRIPT_DIR, 'object_report.csv')
            with open(report_file, 'w', newline='', encoding='utf-8') as _f:
                writer = csv.writer(_f)
                writer.writerow(['Type', 'Name', 'Business Terms', 'Classifications', 'Policies'])
                for _o in r.objects:
                    writer.writerow([_o.shortType, _o.name, _o.getBusinessTermNames(),
                                     _o.getClassificationNames(), _o.getParentPolicyNames()])
            print(f"INFO: Full object report written to {report_file} ({len(r.objects)} objects)")

        for obj in r.objects:
            try:
                debug(f"Looking at path: {obj.getFriendlyId()}")
                db_path_array = obj.getFriendlyId().split('/')
                db_path_array.pop(0)
                db_path_array.pop(0)
                db_path_array.pop(-1)
                obj_parent_path = ".".join(db_path_array)
                obj_name = obj.name
            except:
                continue

            debug(f"Evaluating: {obj.shortType} {obj_parent_path}.{obj_name}")
            debug(f"     Classifications: {obj.getClassificationNames()}")
            debug(f"     Business Terms: {obj.getBusinessTermNames()}")
            debug(f"     Related Policies: {obj.getParentPolicyNames()}")

            if unset_tags_first:
                for unset_tag in unset_tags:
                    if obj.shortType == 'Column':
                        unset_statement = f"ALTER TABLE {obj_parent_path} MODIFY Column {obj_name} UNSET tag {unset_tag}"
                        unset_statements.append(unset_statement)
                        debug(f"Adding unset statement: {unset_statement}")
                    if obj.shortType == 'Table':
                        unset_statement = f"ALTER TABLE {obj_parent_path}.{obj_name} UNSET tag {unset_tag}"
                        unset_statements.append(unset_statement)
                        debug(f"Adding unset statement: {unset_statement}")
                    if obj.shortType == 'View':
                        unset_statement = f"ALTER VIEW {obj_parent_path}.{obj_name} UNSET tag {unset_tag}"
                        unset_statements.append(unset_statement)
                        debug(f"Adding unset statement: {unset_statement}")
                    if obj.shortType == 'ViewColumn':
                        unset_statement = f"ALTER VIEW {obj_parent_path} MODIFY Column {obj_name} UNSET tag {unset_tag}"
                        unset_statements.append(unset_statement)
                        debug(f"Adding unset statement: {unset_statement}")

            if len(obj.getBusinessTermNames()) > 0 and writeback_business_term:
                if obj.shortType.endswith('ViewColumn'):
                    statement = "ALTER VIEW "+obj_parent_path+" MODIFY Column "+obj_name+" SET tag "+writeback_business_term_tag+" = '"+obj.getBusinessTermNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Column'):
                    statement = "ALTER TABLE "+obj_parent_path+" MODIFY Column "+obj_name+" SET tag "+writeback_business_term_tag+" = '"+obj.getBusinessTermNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Table'):
                    statement = "ALTER TABLE "+obj_parent_path+"."+obj_name+" SET tag "+writeback_business_term_tag+" = '"+obj.getBusinessTermNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('View'):
                    statement = "ALTER VIEW "+obj_parent_path+"."+obj_name+" SET tag "+writeback_business_term_tag+" = '"+obj.getBusinessTermNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)

            if len(obj.getParentPolicyNames()) > 1 and writeback_parent_policy:
                if obj.shortType.endswith('ViewColumn'):
                    statement = "ALTER VIEW "+obj_parent_path+" MODIFY Column "+obj_name+" SET tag "+writeback_parent_policy_tag+" = '"+obj.getParentPolicyNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Column'):
                    statement = "ALTER TABLE "+obj_parent_path+" MODIFY Column "+obj_name+" SET tag "+writeback_parent_policy_tag+" = '"+obj.getParentPolicyNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Table'):
                    statement = "ALTER TABLE "+obj_parent_path+"."+obj_name+" SET tag "+writeback_parent_policy_tag+" = '"+obj.getParentPolicyNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('View'):
                    statement = "ALTER VIEW "+obj_parent_path+"."+obj_name+" SET tag "+writeback_parent_policy_tag+" = '"+obj.getParentPolicyNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)

            if len(obj.getClassificationNames()) > 0 and writeback_classification:
                if obj.shortType.endswith('ViewColumn'):
                    statement = "ALTER VIEW "+obj_parent_path+" MODIFY Column "+obj_name+" SET tag "+writeback_classification_tag+" = '"+obj.getClassificationNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Column'):
                    statement = "ALTER TABLE "+obj_parent_path+" MODIFY Column "+obj_name+" SET tag "+writeback_classification_tag+" = '"+obj.getClassificationNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Table'):
                    statement = "ALTER TABLE "+obj_parent_path+"."+obj_name+" SET tag "+writeback_classification_tag+" = '"+obj.getClassificationNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('View'):
                    statement = "ALTER VIEW "+obj_parent_path+"."+obj_name+" SET tag "+writeback_classification_tag+" = '"+obj.getClassificationNames()+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)

            dq_score = dq_scores.get(obj.identity)
            if dq_score is not None and writeback_dq_score:
                dq_score_str = str(dq_score)
                if obj.shortType.endswith('ViewColumn'):
                    statement = "ALTER VIEW "+obj_parent_path+" MODIFY Column "+obj_name+" SET tag "+writeback_dq_score_tag+" = '"+dq_score_str+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Column'):
                    statement = "ALTER TABLE "+obj_parent_path+" MODIFY Column "+obj_name+" SET tag "+writeback_dq_score_tag+" = '"+dq_score_str+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Table'):
                    statement = "ALTER TABLE "+obj_parent_path+"."+obj_name+" SET tag "+writeback_dq_score_tag+" = '"+dq_score_str+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('View'):
                    statement = "ALTER VIEW "+obj_parent_path+"."+obj_name+" SET tag "+writeback_dq_score_tag+" = '"+dq_score_str+"'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)

            if writeback_description and len(obj.description) > 0:
                desc = strip_html(obj.description).replace("'", "''")
                if obj.shortType.endswith('ViewColumn'):
                    statement = f"ALTER VIEW {obj_parent_path} MODIFY COLUMN {obj_name} COMMENT '{desc}'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Column'):
                    statement = f"ALTER TABLE {obj_parent_path} MODIFY COLUMN {obj_name} COMMENT '{desc}'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('Table'):
                    statement = f"ALTER TABLE {obj_parent_path}.{obj_name} SET COMMENT = '{desc}'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)
                elif obj.shortType.endswith('View'):
                    statement = f"ALTER VIEW {obj_parent_path}.{obj_name} SET COMMENT = '{desc}'"
                    statements.append(statement)
                    print("INFO: Adding "+statement)


# ===========================================================================
# Write to Snowflake
# ===========================================================================

if stop_and_verify:
    input("Press any key to continue...")

if sf_private_key_file:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PrivateFormat, NoEncryption
    from cryptography.hazmat.backends import default_backend
    _passphrase = sf_private_key_pass.encode() if sf_private_key_pass else None
    with open(sf_private_key_file, 'rb') as _kf:
        _private_key_obj = load_pem_private_key(_kf.read(), password=_passphrase, backend=default_backend())
    _private_key_bytes = _private_key_obj.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    sf_connect_kwargs = dict(account=sf_account, user=sf_username, private_key=_private_key_bytes, database=sf_database, role=sf_role)
    print(f"INFO: Connecting to Snowflake as {sf_username} using key-pair authentication")
else:
    if len(encrypted_sf_password) > 2:
        sf_password = decrypt_message(encrypted_sf_password)
    sf_connect_kwargs = dict(account=sf_account, user=sf_username, password=sf_password, database=sf_database, role=sf_role)
    print(f"INFO: Connecting to Snowflake as {sf_username} using password authentication")

with snowflake.connector.connect(**sf_connect_kwargs) as conn:
    with conn.cursor() as cursor:
        # Attempt to create any enabled tags — warns and continues if permission is denied
        tags_to_create = []
        if writeback_business_term:
            tags_to_create.append(writeback_business_term_tag)
        if writeback_parent_policy:
            tags_to_create.append(writeback_parent_policy_tag)
        if writeback_classification:
            tags_to_create.append(writeback_classification_tag)
        if writeback_dq_score:
            tags_to_create.append(writeback_dq_score_tag)
        for tag in tags_to_create:
            try:
                cursor.execute(f"CREATE TAG IF NOT EXISTS {tag}")
                print(f"INFO: Tag exists or created: {tag}")
            except Exception as e:
                print(f"WARNING: Could not create tag {tag} — {e}")
                print(f"WARNING:   The tag must be created manually before SET statements will succeed.")

        def execute_with_iceberg_fallback(stmt):
            """Run stmt; if Snowflake rejects it as an Iceberg table, retry with ALTER ICEBERG TABLE."""
            try:
                cursor.execute(stmt)
            except Exception as e:
                if 'iceberg' in str(e).lower() and stmt.upper().startswith('ALTER TABLE'):
                    iceberg_stmt = 'ALTER ICEBERG TABLE' + stmt[len('ALTER TABLE'):]
                    print(f"INFO:   Retrying as Iceberg table: {iceberg_stmt}")
                    cursor.execute(iceberg_stmt)
                else:
                    raise

        if unset_tags_first:
            print(f"INFO: Executing statements to unset these tags: {','.join(unset_tags)}")
            for unset_statement in unset_statements:
                execute_with_iceberg_fallback(unset_statement)
        for statement in statements:
            print("INFO: Executing " + statement)
            execute_with_iceberg_fallback(statement)
