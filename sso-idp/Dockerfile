FROM registry.suse.com/bci/openjdk:21@sha256:b47362664c3d49f21cd20f2375ad7344735b0c29dee76009ed9dbe8e1318a263

USER root
COPY --chown=1000:1000 keycloak/ /opt/keycloak/
USER 1000:1000
WORKDIR /opt/keycloak
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
