FROM python:3.7-alpine
RUN apk add --no-cache git
WORKDIR /kopf-operator
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY vsphere vsphere
COPY controller.py .
RUN adduser -D -h /home/operator -s /bin/bash -G users kopf-operator
USER kopf-operator
# vCenter username/password/host to be used by the operator
ENV VC_USER=
ENV VC_PASS=
ENV VC_HOST=
CMD VC_USER=${VC_USER} VC_PASS=${VC_PASS} VC_HOST=${VC_HOST} kopf run controller.py --verbose