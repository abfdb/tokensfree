"""Offline configuration preflight. No account checks, keys, environment or network.

All input is unverified declaration. Even perfect input NEVER authorizes calls.
"""
import argparse
import json
import math
import sys
import time

PROVIDER = 'google-gemini-developer'
ENDPOINT = 'https://generativelanguage.googleapis.com'
FIELDS = {'provider', 'endpoint', 'model', 'credential_store', 'billing_tier',
          'paid_fallback', 'region_supported', 'data_terms_accepted',
          'free_model_verified', 'hard_stop_verified', 'checked_at'}
CHECKS = (
    ('credential_store', 'os-keyring', 'secure_credential_storage_required'),
    ('billing_tier', 'free', 'free_project_required'),
    ('paid_fallback', False, 'paid_fallback_must_be_disabled'),
    ('region_supported', True, 'region_review_required'),
    ('data_terms_accepted', True, 'data_terms_review_required'),
    ('free_model_verified', True, 'model_free_tier_review_required'),
    ('hard_stop_verified', True, 'platform_hard_stop_review_required'),
)

def inspect_config(config, now=None):
    # Do not echo supplied values/keys: they may be accidental credentials.
    result = {'provider': PROVIDER, 'offline': True, 'account_verified': False,
              'eligible': False, 'can_call': False, 'blockers': [],
              'declaration_checks_complete': False}
    reasons = result['blockers']
    if not isinstance(config, dict) or set(config) != FIELDS:
        reasons.extend(['invalid_schema_no_secrets_allowed', 'real_calls_disabled'])
        return result
    if config['provider'] != PROVIDER: reasons.append('unsupported_provider')
    if config['endpoint'] != ENDPOINT: reasons.append('endpoint_not_allowlisted')
    # Model is only syntax checked. No static catalog may assert current pricing.
    model = config['model']
    import re
    if not isinstance(model, str) or not re.fullmatch(r'gemini-[a-z0-9.-]{1,80}', model):
        reasons.append('invalid_model_identifier')
    for field, expected, reason in CHECKS:
        if type(config[field]) is not type(expected) or config[field] != expected:
            reasons.append(reason)
    now = time.time() if now is None else now
    checked = config['checked_at']
    def valid_number(v):
        try: return type(v) in (int, float) and math.isfinite(v)
        except OverflowError: return False
    if not valid_number(now) or not valid_number(checked) or not 0 <= now - checked <= 86400:
        reasons.append('declaration_stale_or_invalid')
    result['declaration_checks_complete'] = not reasons
    reasons.extend(['unverified_account_declarations', 'credential_binding_not_verified', 'real_calls_disabled'])
    return result

def parse_config(raw):
    if len(raw) > 8192: raise ValueError('size')
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj: raise ValueError('duplicate')
            obj[key] = value
        return obj
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('number')))

def main():
    parser = argparse.ArgumentParser(description='Offline declaration preflight; never enables API calls')
    parser.add_argument('config', help='non-secret JSON configuration file')
    args = parser.parse_args()
    try:
        with open(args.config, 'rb') as handle: raw = handle.read(8193)
        result = inspect_config(parse_config(raw))
    except (OSError, ValueError, TypeError, RecursionError):
        result = {'offline': True, 'eligible': False, 'can_call': False,
                  'blockers': ['invalid_config_no_secrets_allowed', 'real_calls_disabled']}
    print(json.dumps(result, indent=2))
    return 2 # Preflight never grants permission, even for complete declarations.

if __name__ == '__main__': sys.exit(main())
