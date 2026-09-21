"""Local Keycloak SAML lab. AWS configuration is managed separately by Terraform."""

import argparse
import base64
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import subprocess
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "artifacts/sso-idp"
BUILD = ROOT / ".tools/keycloak-build"
DOCKER_CONFIG = ROOT / ".tools/docker"
REALM = "bedrock-cost-lab"
BASE = "https://localhost:8843"
CONTAINER = "bedrock-cost-lab-idp"
VERSION = "26.7.4"
IMAGE = f"bedrock-cost-lab-keycloak:{VERSION}"
ARCHIVE_SHA256 = "04823c336b797a7e18889a44262a7a64e6bf624cbbcc518c85e2622176ff2eee"
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


def credentials():
    return json.loads((STATE / "credentials.json").read_text())


def opener():
    context = ssl.create_default_context(cafile=STATE / "tls.crt")
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )


def request(path, *, data=None, token=None, method=None, form=False, client=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        data = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
    url = path if path.startswith(BASE + "/") else BASE + path
    with (client or opener()).open(
        urllib.request.Request(url, data=data, headers=headers, method=method), timeout=30
    ) as response:
        raw = response.read()
        return json.loads(raw) if raw and "application/json" in response.headers.get("Content-Type", "") else raw


def docker(*args, capture=False):
    return subprocess.run(
        ["docker", "--config", str(DOCKER_CONFIG), *args], check=True,
        capture_output=capture, text=True,
    )


def prepare():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    DOCKER_CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (STATE / "credentials.json").exists():
        private_write(STATE / "credentials.json", json.dumps({
            "admin_username": "lab-admin", "admin_password": secrets.token_urlsafe(30),
            "users": {key: {"username": user["username"], "password": secrets.token_urlsafe(24)}
                      for key, user in USERS.items()},
        }, indent=2) + "\n")
    if not (STATE / "tls.crt").exists():
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes",
            "-days", "30", "-keyout", str(STATE / "tls.key"), "-out", str(STATE / "tls.crt"),
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ], check=True, capture_output=True)
        (STATE / "tls.key").chmod(0o600)
    (STATE / "data").mkdir(exist_ok=True, mode=0o700)
    cred = credentials()
    private_write(STATE / "container.env",
                  f"KC_BOOTSTRAP_ADMIN_USERNAME={cred['admin_username']}\n"
                  f"KC_BOOTSTRAP_ADMIN_PASSWORD={cred['admin_password']}\n")
    config = json.loads((ROOT / "artifacts/test-config.json").read_text())
    private_write(ROOT / "sso-lab/local.auto.tfvars.json", json.dumps({
        key: config[key] for key in ("management_account_id", "member_account_id", "region")
    }, indent=2) + "\n")
    backend = (ROOT / "bedrock-lab/local.backend.hcl").read_text()
    if '"bedrock-lab/terraform.tfstate"' not in backend:
        raise RuntimeError("Unexpected source backend key; refusing to reuse another root's state.")
    private_write(ROOT / "sso-lab/local.backend.hcl",
                  backend.replace("bedrock-lab/terraform.tfstate", "sso-lab/terraform.tfstate"))
    print("Prepared private credentials, localhost TLS, and SSO Terraform inputs.")


def build():
    BUILD.mkdir(parents=True, exist_ok=True)
    archive = BUILD / f"keycloak-{VERSION}.tar.gz"
    if not archive.exists():
        url = f"https://github.com/keycloak/keycloak/releases/download/{VERSION}/{archive.name}"
        with urllib.request.urlopen(url) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != ARCHIVE_SHA256:
        raise RuntimeError("Keycloak archive differs from the upstream release SHA256 digest.")
    target = BUILD / "keycloak"
    if not target.exists():
        with tarfile.open(archive) as source:
            source.extractall(BUILD, filter="data")
        (BUILD / f"keycloak-{VERSION}").rename(target)
    docker("build", "--tag", IMAGE, "--file", str(ROOT / "sso-idp/Dockerfile"), str(BUILD))


def start():
    found = docker("ps", "-a", "--filter", f"name=^/{CONTAINER}$", "--format", "{{.Names}}", capture=True)
    if found.stdout.strip():
        label = docker("inspect", "--format", '{{index .Config.Labels "bedrock-cost-lab.component"}}',
                       CONTAINER, capture=True).stdout.strip()
        if label != "sso-idp":
            raise RuntimeError("An unrelated container already uses the lab name.")
        docker("start", CONTAINER)
        return
    docker(
        "run", "-d", "--name", CONTAINER, "--label", "bedrock-cost-lab.component=sso-idp",
        "--publish", "127.0.0.1:8843:8443", "--memory", "2g", "--cpus", "2",
        "--user", f"{os.getuid()}:{os.getgid()}", "--env-file", str(STATE / "container.env"),
        "--volume", f"{STATE / 'data'}:/opt/keycloak/data:Z",
        "--volume", f"{STATE / 'tls.crt'}:/run/tls.crt:ro,Z",
        "--volume", f"{STATE / 'tls.key'}:/run/tls.key:ro,Z",
        IMAGE, "start-dev", "--http-host=0.0.0.0", "--http-enabled=false", f"--hostname={BASE}",
        "--https-certificate-file=/run/tls.crt", "--https-certificate-key-file=/run/tls.key",
    )
    print(f"Started the local IdP at {BASE}; allow startup to finish before configuring.")


def admin_token():
    cred = credentials()
    return request("/realms/master/protocol/openid-connect/token", form=True, data={
        "client_id": "admin-cli", "grant_type": "password",
        "username": cred["admin_username"], "password": cred["admin_password"],
    })["access_token"]


def saml_client(entity_id, acs, name):
    return {
        "clientId": entity_id, "name": name, "enabled": True, "protocol": "saml",
        "redirectUris": [acs], "baseUrl": acs,
        "attributes": {
            "saml_assertion_consumer_url_post": acs, "saml.force.post.binding": "true",
            "saml.assertion.signature": "true", "saml.server.signature": "true",
            "saml.signature.algorithm": "RSA_SHA256", "saml.client.signature": "false",
            "saml_name_id_format": "email", "saml_force_name_id_format": "true",
            "saml.authnstatement": "true", "saml.assertion.lifespan": "120",
            "saml_idp_initiated_sso_url_name": name,
        },
        "protocolMappers": [{
            "name": f"AWS {key}", "protocol": "saml",
            "protocolMapper": "saml-user-attribute-mapper",
            "config": {"user.attribute": key, "attribute.name": ACCESS_CONTROL + key,
                       "attribute.nameformat": "Basic"},
        } for key in ("owner", "product")],
    }


def put_client(token, client):
    path = f"/admin/realms/{REALM}/clients"
    found = request(path + "?" + urllib.parse.urlencode({"clientId": client["clientId"]}), token=token)
    if found:
        request(path + "/" + found[0]["id"], method="PUT", data=client, token=token)
    else:
        request(path, data=client, token=token)


def configure(sp_metadata=None):
    token = admin_token()
    path = f"/admin/realms/{REALM}"
    try:
        request(path, token=token)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        request("/admin/realms", token=token, data={
            "realm": REALM, "enabled": True, "registrationAllowed": False,
            "resetPasswordAllowed": False, "editUsernameAllowed": False,
            "bruteForceProtected": True, "sslRequired": "all",
        })
    profile = request(path + "/users/profile", token=token)
    profile["unmanagedAttributePolicy"] = None
    profile["attributes"] = [a for a in profile["attributes"] if a["name"] not in ("owner", "product")]
    profile["attributes"].extend({
        "name": key, "displayName": key,
        "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
        "validations": {"length": {"max": 128}},
    } for key in ("owner", "product"))
    request(path + "/users/profile", method="PUT", token=token, data=profile)
    stored = request(path + "/users/profile", token=token)
    for key in ("owner", "product"):
        attribute = next(a for a in stored["attributes"] if a["name"] == key)
        if attribute.get("permissions", {}).get("edit") != ["admin"]:
            raise RuntimeError(f"{key}: the source attribute must be editable only by an administrator.")
    cred = credentials()
    for key, user in USERS.items():
        found = request(path + "/users?" + urllib.parse.urlencode({
            "username": user["username"], "exact": "true"
        }), token=token)
        payload = {
            "username": user["username"], "email": user["username"], "enabled": True,
            "emailVerified": True, "firstName": user["given_name"], "lastName": user["family_name"],
            "attributes": {k: [v] for k, v in user["attributes"].items()},
        }
        if found:
            request(path + "/users/" + found[0]["id"], method="PUT", token=token, data=payload)
        else:
            payload["credentials"] = [{"type": "password", "temporary": False,
                                       "value": cred["users"][key]["password"]}]
            request(path + "/users", token=token, data=payload)
    put_client(token, saml_client("urn:bedrock-cost-lab:preview", BASE + "/preview-acs", "preview"))
    if sp_metadata:
        metadata = ET.parse(sp_metadata).getroot()
        descriptor = metadata if metadata.tag.endswith("EntityDescriptor") else metadata.find("m:EntityDescriptor", SAML)
        acs = next(node.attrib["Location"] for node in descriptor.findall(".//m:AssertionConsumerService", SAML)
                   if node.attrib["Binding"].endswith("HTTP-POST"))
        if not acs.startswith("https://"):
            raise ValueError("The AWS SAML ACS must use HTTPS.")
        put_client(token, saml_client(descriptor.attrib["entityID"], acs, "aws"))
    metadata = request(f"/realms/{REALM}/protocol/saml/descriptor")
    private_write(STATE / "idp-metadata.xml", metadata.decode())
    print("Configured three users and automatic SAML attribute mappings; exported IdP metadata.")


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

    metadata = ET.fromstring((STATE / "idp-metadata.xml").read_bytes())
    certs = [node.text.strip() for node in metadata.findall(".//d:X509Certificate", SAML)]
    evidence = []
    for key, user in USERS.items():
        client = opener()
        html = request(f"/realms/{REALM}/protocol/saml/clients/preview", client=client)
        login = Form()
        login.feed(html.decode())
        if not login.action or not login.action.startswith(BASE + "/"):
            raise RuntimeError("Expected a local Keycloak login form.")
        login.fields.update(username=user["username"], password=credentials()["users"][key]["password"])
        html = request(login.action, data=login.fields, form=True, client=client)
        posted = Form()
        posted.feed(html.decode())
        if "SAMLResponse" not in posted.fields:
            raise RuntimeError(f"{key}: login did not issue a SAML response.")
        xml = base64.b64decode(posted.fields["SAMLResponse"])
        verified = None
        for cert in certs:
            try:
                verified = XMLVerifier().verify(xml, x509_cert=cert).signed_xml
                break
            except Exception:
                continue
        if verified is None:
            raise RuntimeError(f"{key}: SAML signature did not match the IdP metadata.")
        issuer = verified.find(".//s:Issuer", SAML)
        audience = verified.find(".//s:Audience", SAML)
        if (
            issuer is None or issuer.text != metadata.attrib["entityID"]
            or audience is None or audience.text != "urn:bedrock-cost-lab:preview"
        ):
            raise RuntimeError(f"{key}: unexpected SAML issuer or audience.")
        name = verified.find(".//s:NameID", SAML)
        attributes = {
            node.attrib["Name"]: [value.text for value in node.findall("s:AttributeValue", SAML)]
            for node in verified.findall(".//s:Attribute", SAML)
        }
        actual = {name.removeprefix(ACCESS_CONTROL): values[0] for name, values in attributes.items()
                  if name.startswith(ACCESS_CONTROL) and len(values) == 1}
        if name is None or name.text != user["username"] or actual != user["attributes"]:
            raise RuntimeError(f"{key}: unexpected NameID or automatic SAML attributes.")
        evidence.append({"user": key, "name_id": name.text, "attributes": actual, "signature_verified": True})
    private_write(STATE / "login-verification.json", json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


def profiles(start_url):
    url = urllib.parse.urlparse(start_url)
    if (
        url.scheme != "https" or not (url.hostname or "").endswith(".awsapps.com")
        or "\n" in start_url or "\r" in start_url
    ):
        raise ValueError("Use the HTTPS AWS access portal URL shown by Identity Center.")
    config = json.loads((ROOT / "artifacts/test-config.json").read_text())
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
    for command in ("prepare", "build", "start", "verify-logins"):
        sub.add_parser(command)
    configure_parser = sub.add_parser("configure")
    configure_parser.add_argument("--sp-metadata", type=Path)
    profiles_parser = sub.add_parser("profiles")
    profiles_parser.add_argument("--start-url", required=True)
    args = parser.parse_args()
    if args.command == "configure":
        configure(args.sp_metadata)
    elif args.command == "profiles":
        profiles(args.start_url)
    else:
        {"prepare": prepare, "build": build, "start": start, "verify-logins": verify_logins}[args.command]()


if __name__ == "__main__":
    main()
