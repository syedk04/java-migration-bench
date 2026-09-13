FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
        ca-certificates \
    && apt-get install -y --no-install-recommends openjdk-8-jdk-headless \
    && apt-get install -y --no-install-recommends openjdk-17-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

ENV MAVEN_VERSION=3.9.6
ENV MAVEN_HOME=/opt/maven
RUN curl -fsSL "https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz" \
        -o /tmp/maven.tar.gz \
    && tar -xzf /tmp/maven.tar.gz -C /opt \
    && mv /opt/apache-maven-${MAVEN_VERSION} ${MAVEN_HOME} \
    && rm /tmp/maven.tar.gz

ENV PATH="${MAVEN_HOME}/bin:${PATH}"
ENV JAVA_8_HOME=/usr/lib/jvm/java-8-openjdk-amd64
ENV JAVA_17_HOME=/usr/lib/jvm/java-17-openjdk-amd64

COPY use-java.sh /usr/local/bin/use-java.sh
RUN chmod +x /usr/local/bin/use-java.sh

# default to Java 8 so a plain `mvn` invocation matches the repos' base state
ENV JAVA_HOME=${JAVA_8_HOME}
ENV PATH="${JAVA_HOME}/bin:${PATH}"

# shared Maven local repo, bind-mount this from the host across every run
VOLUME /root/.m2

WORKDIR /workspace
