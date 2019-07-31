"""
Controller implements the control loop logic for the kopf-operator
"""
from typing import Dict
import atexit, sys, os, logging
from time import gmtime, strftime, sleep
import kopf
from pyVim.connect import Disconnect
from pyVmomi import vim
from vsphere import vsphere

# vcenter configuration
VC_USER = os.getenv('VC_USER')
VC_PASS = os.getenv('VC_PASS')
VC_HOST = os.getenv('VC_HOST')
VC_PORT = os.getenv('VC_PORT', 443)
INSECURE = True

# hard-coded template settings to clone configuration
TEMPLATE_FOLDER = "kopf-vmworld"
DATACENTER = "vcqaDC"
CLUSTER = "cls"
DATASTORE = "sharedVmfs-0"

# register kopf handlers
@kopf.on.event('vsphere.vmware.com', 'v1alpha1', 'vmgroups')
def vm_operator(event, spec, meta, status, logger, **_):
    # poor man's throttle: give vcenter operations some time to catch up
    sleep(3)

    vmgroup = meta.get('name')
    template = spec.get('template')
    desired_replicas = spec.get('replicas')
    now = strftime("%Y-%m-%d %H:%M:%S", gmtime())

    event_type = event['type']
    if event_type == "DELETED":
        logger.info(f'deleting vm group "{vmgroup}"')
        # TODO: if something goes wrong deleting the folder/VMs, we only log the exception it in the function since the custom resource in Kubernetes is gone
        # needs garbage collection routine for vCenter objects
        delete_vm_group(vmgroup, logger)
        return
    
    # ADDED/MODIFIED event
    try:
        phase = status['vm_operator']['phase']
    # if we don't find a status, initialize the object and set its state to "PENDING"
    except KeyError:
        phase = "PENDING"
        
    if phase == "PENDING":
        if template == "":
            phase = "ERROR"
            return set_status(phase, now, 'no template speficied, check your deployment spec', 0, desired_replicas)
            
        valid_template = validate_template(template, logger)
        if not valid_template:
            phase = "ERROR"
            return set_status(phase, now, f'invalid template "{template}" specified', 0, desired_replicas)

        # check if VM group already exists
        exists = vm_group_exists(vmgroup)

        # if exists -> sync current with desired replicas
        if exists:
            current_replicas = get_replicas(vmgroup)
            new_count = sync_replica_count(vmgroup, spec, current_replicas, desired_replicas, logger)
            if current_replicas == new_count:
                phase = "READY"
            return set_status(phase, now, 'synced replica count', new_count, desired_replicas)

        if not exists:
            new_count = create_vm_group(vmgroup, spec, logger)
            if new_count < 0:
                phase = "ERROR"
                return set_status(phase, now, 'error creating vm group instances (check kopf vsphere operator logs)', new_count, desired_replicas)
            elif new_count == desired_replicas:
                phase = "READY"
                return set_status(phase, now, f'successfully deployed vm group "{vmgroup}"', new_count, desired_replicas)
            else:
                return set_status(phase, now, f'created vm group "{vmgroup}" and waiting for VMs to become ready', new_count, desired_replicas)

    # if we are in an ERROR state, don't retry (TODO: FIXME error handling :) and leave last error message as is for debugging
    elif phase == "ERROR":
        return
    
    # check if someone updated the replica count since the last successful deployment
    elif phase == "READY":
        # sanity check if VM group already exists
        exists = vm_group_exists(vmgroup)

        if exists:
            current_replicas = get_replicas(vmgroup)
            if current_replicas != desired_replicas:
                # set phase to "PENDING" and return so it will be resubmitted for event processing and caught by the logic above
                phase = "PENDING"
                return set_status(phase, now, f'vm group "{vmgroup} out of sync, submitting for resync"', current_replicas, desired_replicas)
        # if the vm group does not exist in vcenter but in Kubernetes, we've messed up - needs operator intervention
        else:
            phase = "ERROR"
            return set_status(phase, now, 'kopf vsphere operator out of sync: custom resource exists but object not found in vcenter', new_count, desired_replicas)

def validate_template(template: str, logger: logging.Logger) -> bool:
    """
    validate_template checks whether the template exists
    """
    valid = False
    try:
        valid = vsphere.find_template(content, dc, template)
    except vim.fault.ManagedObjectNotFound as e:
        logger.warn(f'template "{template}" not found: {str(e)}')
    return valid

def vm_group_exists(vm_group: str) -> bool:
    """
    vm_group_exists checks whether a VM group "vm_group" already exists in a pre-defined content and datacenter
    """
    return vsphere.vm_group_exists(content, dc, vm_group)

def delete_vm_group(vm_group: str, logger: logging.Logger):
    """
    delete_vm_group deletes a VM group "vm_group" from a pre-defined content and datacenter
    """
    try:
        vsphere.delete_folder(content, dc, vm_group)
    except vsphere.Error as e:
        logger.warn(str(e))

def create_vm_group(vmgroup_name: str, vmgroup_spec: Dict[str, str], logger: logging.Logger) -> int:
    """
    create_vm_group creates a VM group "vm_group" in a pre-defined datacenter
    """
    try:
        vsphere.create_folder(dc, vmgroup_name)
    except vsphere.ObjectAlreadyExists as e:
        logger.warn(str(e))
        return

    try:
        created = vsphere.clone_vm(content, dc, CLUSTER, DATASTORE, vmgroup_name, vmgroup_spec, logger)
    except vsphere.CloneError as e:
        logger.warn(str(e))
        return -1

    return created

def sync_replica_count(vmgroup_name: str, vmgroup_spec: Dict[str, str], current: int, desired: int, logger: logging.Logger) -> int:
    """
    sync_replica_count synchronises the current with the desired replica count for a VM group "vmgroup_name" 
    and VM group spec "vmgroup_spec" in a pre-defined content and datacenter
    """
    if current > desired:
        delete_instances = current - desired
        deleted = delete_replicas(vmgroup_name, delete_instances, logger)
        return current - deleted
    elif current < desired:
        diff = desired - current
        # since we're using the generic clone_vm call on an existing vm group we have to change the replica count to only create "diff" number of instances
        vmgroup_spec['replicas'] = diff
        created = 0
        try:
            created = vsphere.clone_vm(content, dc, CLUSTER, DATASTORE, vmgroup_name, vmgroup_spec, logger)
        except vsphere.CloneError as e:
            # log the error but allow retry (TODO: this is probably not ok if the error is permanent)
            logger.warn(str(e))
        return current + created
    # desired = current, nothing to do
    else:
        return current

def delete_replicas(vm_group: str, to_delete: int, logger: logging.Logger) -> int:
    """
    delete_replicas deletes the number of "to_delete" instances (replicas) from a VM group "vm_group" in a pre-defined content and datacenter
    """
    deleted = 0
    try: 
        deleted = vsphere.delete_replicas(content, dc, vm_group, to_delete, logger)
    except vsphere.DestroyError as e:
        # log the error but allow retry (TODO: this is probably not ok if the error is permanent)
        logger.warn(str(e))
    return deleted

def get_replicas(vm_group: str) -> int:
    """
    get_replicas retrieves the number of deployed instances (replicas) of a VM group "vm_group" in a pre-defined content and datacenter
    """
    return vsphere.get_current_replicas(content, dc, vm_group)

def set_status(phase: str, timestamp: str, message: str, current: int, desired: int) -> dict:
    """
    set_status is a convinience function to return status messages
    used by kopf to update a custom resource status
    """
    return {
        'phase': phase, 'lastUpdated': timestamp, 'currentReplicas': current, 'desiredReplicas': desired, 'lastMessage': message}

# establish connection to vsphere
session = None
try:
    session = vsphere.connectvc(VC_HOST, VC_USER, VC_PASS,VC_PORT, INSECURE)
except Exception as e:
    print(f"could not connect to vcenter: {e}")
    sys.exit(1)

# disconnect from vcenter when shutting down the controller
atexit.register(Disconnect, session)
content = session.RetrieveContent()

try:
    dc = vsphere.get_datacenter(content, DATACENTER)
except vsphere.ObjectNotFoundError as e:
    print(str(e))
    sys.exit(1)
    