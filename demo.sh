#!/usr/bin/env bash

################################################
# include the magic using a working version
################################################
test -s ./demo-magic.sh || curl --silent https://raw.githubusercontent.com/paxtonhare/demo-magic/fc286d6f7adf86804fd207e26d8b1d10ab35b807/demo-magic.sh > demo-magic.sh
# shellcheck disable=SC1091
. ./demo-magic.sh

################################################
# Configure the options
################################################

#
# speed at which to simulate typing. bigger num = faster
#
export TYPE_SPEED=50

# Uncomment to run non-interactively
#export PROMPT_TIMEOUT=2

#
# custom prompt
#
# see http://www.tldp.org/HOWTO/Bash-Prompt-HOWTO/bash-prompt-escape-sequences.html for escape sequences
#
#DEMO_PROMPT="${GREEN}➜ ${CYAN}\W "
export DEMO_PROMPT="${GREEN}➜ ${CYAN}$ "

# fix bug in demo magic, explanation here https://unix.stackexchange.com/posts/105946/revisions
# this will make pe/p actually wait
unset NO_WAIT

# hide the evidence
clear

# register the CRD
pe "kubectl create -f config/crd/bases/vmworld.tanzu.vmware.com_vmgroups.yaml"

# deploy the first example
pe "kubectl create -f examples/example.yaml"

# retrieve status
pe "kubectl describe vg vg-1"

# scale down the group
pe "kubectl scale --replicas 1 vg vg-1"

# create second example and show how it fails
pe "kubectl create -f examples/example2.yaml"

# fix the template error
pe "kubectl patch vg vg-2 --type merge -p '{\"spec\":{\"template\":\"kopf-vm-template\"}}'"

# delete all vgs
pe "kubectl delete vg --all"

# delete CRD
p ""
