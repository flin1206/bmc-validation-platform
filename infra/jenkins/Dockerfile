# Jenkins controller that can also run the 'qemu' workload itself.
# Good enough for a single-machine lab; production splits this into a
# controller plus Packer-built agents (see infra/packer).
FROM jenkins/jenkins:lts-jdk17

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
      qemu-system-arm ipmitool squashfs-tools python3 python3-venv python3-pip \
      curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

ARG SYFT_VERSION=1.52.0
ARG GRYPE_VERSION=0.119.0
RUN curl -sSfL "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_amd64.tar.gz" \
      | tar -xz -C /usr/local/bin syft \
 && curl -sSfL "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_linux_amd64.tar.gz" \
      | tar -xz -C /usr/local/bin grype

USER jenkins
COPY --chown=jenkins:jenkins plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Dhudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true"
ENV CASC_JENKINS_CONFIG=/var/jenkins_home/casc.yaml
COPY --chown=jenkins:jenkins casc.yaml /var/jenkins_home/casc.yaml
