"""Use the Terraform-managed sandbox Keycloak realm for the personal AWS SSO lab."""

import argparse
import base64
from datetime import datetime, timezone
from html.parser import HTMLParser
import http.cookiejar
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "artifacts/sso-idp"
REALM = "bedrock-cost-lab"
BASE = "https://sso.sandbox-main.sandbox.stackstate.io"
REALM_PATH = f"/realms/{REALM}/"
ISSUER = BASE + REALM_PATH.rstrip("/")
USERS = json.loads((ROOT / "sso-idp/users.json").read_text())
ACCESS_CONTROL = "https://aws.amazon.com/SAML/Attributes/AccessControl:"
SAML = {"s": "urn:oasis:names:tc:SAML:2.0:assertion",
        "m": "urn:oasis:names:tc:SAML:2.0:metadata",
        "d": "http://www.w3.org/2000/09/xmldsig#"}


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        stream.write(text)
    path.chmod(0o600)


def realm_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https" or parsed.netloc != urllib.parse.urlsplit(BASE).netloc
        or not parsed.path.startswith(REALM_PATH)
    ):
        raise ValueError("Requests must stay within the sandbox lab realm.")
    return url


class RealmRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, realm_url(newurl))


def opener():
    return urllib.request.build_opener(
        RealmRedirectHandler(),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )


def request(url, *, fields=None, client=None):
    realm_url(url)
    if fields is not None and not urllib.parse.urlsplit(url).path.startswith(REALM_PATH + "login-actions/"):
        raise ValueError("Only lab password-login forms may be submitted.")
    data = urllib.parse.urlencode(fields).encode() if fields is not None else None
    with (client or opener()).open(url, data=data, timeout=30) as response:
        return response.read()


def validate_passwords(values):
    if (
        not isinstance(values, dict) or set(values) != set(USERS)
        or any(not isinstance(value, str) or not value for value in values.values())
    ):
        raise ValueError("Expected passwords for alice, bob and untagged.")
    return values


def passwords():
    path = STATE / "sandbox-passwords.json"
    if not path.exists():
        raise ValueError("Run prepare --passwords-sops with the sandbox lab's encrypted password file.")
    return validate_passwords(json.loads(path.read_text()))


def prepare(passwords_sops=None):
    if passwords_sops:
        if not passwords_sops.is_file():
            raise ValueError("Encrypted password file not found; use the applied terraform-infra revision.")
        try:
            result = subprocess.run(
                ["sops", "--decrypt", str(passwords_sops)], capture_output=True, text=True,
                timeout=60, check=True,
            )
        except subprocess.CalledProcessError:
            raise RuntimeError("SOPS decryption failed; check AWS_PROFILE and access to the repository KMS key.") from None
        values = validate_passwords(json.loads(result.stdout))
        private_write(STATE / "sandbox-passwords.json", json.dumps(values, indent=2) + "\n")
    if not all((ROOT / "sso-lab" / name).is_file() for name in ("local.auto.tfvars.json", "local.backend.hcl")):
        raise ValueError("Run scripts/configure.py first to prepare the SSO Terraform inputs.")
    print("SSO Terraform inputs are ready" + ("; loaded private sandbox passwords." if passwords_sops else "."))


def service_provider(path):
    metadata = ET.parse(path).getroot()
    descriptor = metadata if metadata.tag.endswith("}EntityDescriptor") else metadata.find("m:EntityDescriptor", SAML)
    if descriptor is None:
        raise ValueError("Missing AWS service-provider EntityDescriptor.")
    acs_urls = [
        node.attrib["Location"] for node in descriptor.findall(".//m:AssertionConsumerService", SAML)
        if node.attrib["Binding"].endswith("HTTP-POST")
    ]
    if not acs_urls or any(urllib.parse.urlsplit(url).scheme != "https" for url in acs_urls):
        raise ValueError("Expected HTTPS POST assertion-consumer URLs.")
    return descriptor.attrib["entityID"], acs_urls


def configure(sp_metadata=None):
    if sp_metadata:
        service_provider(sp_metadata)
        if sp_metadata.resolve() != (STATE / "aws-sp-metadata.xml").resolve():
            private_write(STATE / "aws-sp-metadata.xml", sp_metadata.read_text())
    raw = request(ISSUER + "/protocol/saml/descriptor")
    metadata = ET.fromstring(raw)
    if metadata.attrib.get("entityID") != ISSUER:
        raise RuntimeError("Unexpected sandbox metadata issuer.")
    path = STATE / "idp-metadata.xml"
    previous = STATE / "idp-metadata.previous.xml"
    if path.exists() and ET.parse(path).getroot().attrib.get("entityID") != ISSUER and not previous.exists():
        private_write(previous, path.read_text())
    private_write(path, raw.decode())
    print("Downloaded sandbox IdP metadata to artifacts/sso-idp/idp-metadata.xml.")
    print("Keycloak configuration is managed by terraform-infra/keycloak/sandbox-main.")


class Form(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form" and self.action is None:
            self.action = attrs.get("action")
        if tag == "input" and "name" in attrs:
            self.fields[attrs["name"]] = attrs.get("value", "")


def verify_logins():
    from signxml import XMLVerifier

    metadata = ET.fromstring(request(ISSUER + "/protocol/saml/descriptor"))
    if metadata.attrib.get("entityID") != ISSUER:
        raise RuntimeError("Unexpected sandbox metadata issuer.")
    certs = [node.text.strip() for node in metadata.findall(".//d:X509Certificate", SAML)]
    clients = {"preview": ("urn:bedrock-cost-lab:preview", [ISSUER + "/preview-acs"])}
    if (STATE / "aws-sp-metadata.xml").exists():
        clients["aws"] = service_provider(STATE / "aws-sp-metadata.xml")
    credentials = passwords()
    evidence = []
    for key, user in USERS.items():
        for client_name, (audience, destinations) in clients.items():
            client = opener()
            login = Form()
            login.feed(request(ISSUER + f"/protocol/saml/clients/{client_name}", client=client).decode())
            if not login.action:
                raise RuntimeError(f"{key}: missing Keycloak login form.")
            login.fields.update(username=user["username"], password=credentials[key])
            posted = Form()
            posted.feed(request(login.action, fields=login.fields, client=client).decode())
            if posted.action not in destinations or "SAMLResponse" not in posted.fields:
                raise RuntimeError(f"{key}/{client_name}: unexpected SAML destination or failed login.")
            xml = base64.b64decode(posted.fields["SAMLResponse"])
            verified = None
            for cert in certs:
                try:
                    verified = XMLVerifier().verify(xml, x509_cert=cert).signed_xml
                    break
                except Exception:
                    continue
            if verified is None:
                raise RuntimeError(f"{key}/{client_name}: SAML signature did not match the IdP metadata.")
            if (
                verified.attrib.get("Destination") != posted.action
                or verified.findtext(".//s:Issuer", namespaces=SAML) != ISSUER
                or verified.findtext(".//s:Audience", namespaces=SAML) != audience
                or verified.findtext(".//s:NameID", namespaces=SAML) != user["username"]
            ):
                raise RuntimeError(f"{key}/{client_name}: unexpected signed identity, destination or audience.")
            conditions = verified.find(".//s:Conditions", SAML)
            now = datetime.now(timezone.utc)
            if conditions is None or not (
                datetime.fromisoformat(conditions.attrib["NotBefore"]) <= now
                < datetime.fromisoformat(conditions.attrib["NotOnOrAfter"])
            ):
                raise RuntimeError(f"{key}/{client_name}: assertion is outside its validity interval.")
            actual = {}
            for node in verified.findall(".//s:Attribute", SAML):
                name = node.attrib["Name"]
                if not name.startswith(ACCESS_CONTROL):
                    continue
                name = name.removeprefix(ACCESS_CONTROL)
                values = node.findall("s:AttributeValue", SAML)
                if name in actual or len(values) != 1:
                    raise RuntimeError(f"{key}/{client_name}: ambiguous attribution attribute.")
                actual[name] = values[0].text
            if actual != user["attributes"]:
                raise RuntimeError(f"{key}/{client_name}: unexpected automatic SAML attributes.")
        evidence.append({
            "user": key, "name_id": user["username"], "attributes": user["attributes"],
            "issuer": ISSUER, "clients_verified": list(clients), "signature_verified": True,
        })
    private_write(STATE / "login-verification.json", json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


def profiles(start_url):
    url = urllib.parse.urlparse(start_url)
    hostname = url.hostname or ""
    portal_host = hostname.endswith(".awsapps.com") or re.fullmatch(
        r"ssoins-[0-9a-f]{16}\.portal\.[a-z0-9-]+\.app\.aws", hostname
    )
    if (
        url.scheme != "https" or not portal_host
        or url.username is not None or url.password is not None
        or url.port not in (None, 443)
        or any(character.isspace() or ord(character) < 32 for character in start_url)
    ):
        raise ValueError("Use the HTTPS AWS access portal URL shown by Identity Center.")
    config = json.loads((ROOT / "sso-lab/local.auto.tfvars.json").read_text())
    sections = []
    for key in USERS:
        sections.append(
            f"[profile lab-{key}]\n"
            f"sso_session = bedrock-lab-{key}\n"
            f"sso_account_id = {config['member_account_id']}\n"
            "sso_role_name = BedrockCostLab\n"
            f"region = {config['region']}\n\n"
            f"[sso-session bedrock-lab-{key}]\n"
            f"sso_start_url = {start_url}\n"
            f"sso_region = {config['region']}\n"
            "sso_registration_scopes = sso:account:access\n"
        )
    private_write(STATE / "aws-config", "\n".join(sections))
    print("Wrote isolated lab profiles to artifacts/sso-idp/aws-config.")



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--passwords-sops", type=Path)
    sub.add_parser("verify-logins")
    configure_parser = sub.add_parser("configure", help="Download metadata; does not administer Keycloak.")
    configure_parser.add_argument("--sp-metadata", type=Path)
    profiles_parser = sub.add_parser("profiles")
    profiles_parser.add_argument("--start-url", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.passwords_sops)
    elif args.command == "configure":
        configure(args.sp_metadata)
    elif args.command == "profiles":
        profiles(args.start_url)
    else:
        verify_logins()


if __name__ == "__main__":
    main()
