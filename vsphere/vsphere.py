# -*- coding: utf-8 -*-
"""
Package vsphere provides convinience methods for interacting with vCenter
"""
from typing import List, Dict
from time import sleep
import logging
from pyVim.connect import SmartConnect, SmartConnectNoSSL
from pyVmomi import vim, vmodl
import pydng

class Error(Exception):
    """Base class for other exceptions"""

class ObjectNotFoundError(Error):
    """Raised when the object was not found"""

class ObjectAlreadyExists(Error):
    """Raised when the object already exists"""

class CloneError(Error):
    """Raised when there is an exception cloning a VM"""

class DestroyError(Error):
    """Raised when there is an exception destroying a VM or VM folder"""

def wait_for_task(task):
    """ Wait for a vCenter task to finish """
    task_done = False
    while not task_done:
        if task.info.state == 'success':
            return task.info.result

        if task.info.state == 'error':
            print("there was an error")
            task_done = True

def get_obj(content, dc: vim.Datacenter, vimtype: List[int], name: str) -> vim.view.ContainerView:
    """
    Return an object by name, if name is None the
    first found object is returned
    """
    obj = None
    container = content.viewManager.CreateContainerView(
        dc, vimtype, True)
    for c in container.view:
        if name:
            if c.name == name:
                obj = c
                break
        else:
            obj = c
            break

    return obj

def connectvc(host, user, password, port, insecure) -> SmartConnect:
    """
    connectvc establishes returns vim.SmartConnect to interact with vCenter
    """
    # try to connect to vcenter
    si = None
    if insecure:
        si = SmartConnectNoSSL(
            host=host,
            user=user,
            pwd=password,
            port=port)
    else:
        si = SmartConnect(
            host=host,
            user=user,
            pwd=password,
            port=port)

    return si

def get_datacenter(content, datacenter: str) -> vim.Datacenter:
    """
    get_datacenter returns a vim.Datacenter for the given content and "datacenter" name
    """
    obj = None
    container = content.viewManager.CreateContainerView(content.rootFolder, [vim.Datacenter], True)
    for c in container.view:
        if c.name == datacenter:
            obj = c
            break
    
    if obj is None:
        raise ObjectNotFoundError(f'Datacenter "{datacenter}" not found')

    return obj

def find_template(content, datacenter: str, template: str) -> bool:
    """
    find_template checks whether the given template "template" exists in the given content and "datacenter" name
    """
    obj = get_obj(content, datacenter, [vim.VirtualMachine], template)
    if obj:
        return True
    else:
        return False

def clone_vm(content, dc: vim.Datacenter, cluster_name: str, datastore_name: str, vmgroup_name: str, vmgroup_config: Dict[str, str], logger: logging.Logger) -> int:
    """
    Creates VMs from a vmgroup configuration spec, returning the number of clones created
    """

    destfolder = get_obj(content, dc, [vim.Folder], vmgroup_name)
    datastore = get_obj(content, dc, [vim.Datastore], datastore_name)
    cluster = get_obj(content, dc, [vim.ClusterComputeResource], cluster_name)
    resource_pool = cluster.resourcePool
    template = get_obj(content, dc, [vim.VirtualMachine], vmgroup_config['template'])


    # set relospec
    relospec = vim.vm.RelocateSpec()
    relospec.datastore = datastore
    relospec.pool = resource_pool

    # set configspec
    configspec = vim.vm.ConfigSpec()
    configspec.numCPUs = vmgroup_config['cpu']
    configspec.memoryMB = 1024 * vmgroup_config['memory']

    clonespec = vim.vm.CloneSpec()
    clonespec.config = configspec
    clonespec.location = relospec
    clonespec.powerOn = True

    vms = list()
    counter = 0
    while counter < vmgroup_config['replicas']:
        name = pydng.generate_name()
        logger.info(f'creating VM "{name}" from template "{template}"')
        try:
            template.Clone(folder=destfolder, name=name, spec=clonespec)
            vms.append(name)
            counter += 1
        except vim.fault.VimFault as e:
            raise CloneError(str(e))

    return len(vms)

def get_current_replicas(content, dc: vim.Datacenter, vmgroup_name: str) -> int:
    """
    get_current_replicas return the number of deployed VMs for the vmgroup name specified in datacenter "dc"
    """
    folder = get_obj(content, dc, [vim.Folder], vmgroup_name)
    if folder is None:
        raise ObjectNotFoundError(f'VM group "{vmgroup_name}" not found')

    vms = list()
    for vm in folder.childEntity:
        if isinstance(vm, vim.VirtualMachine):
            vms.append(vm)

    return len(vms)

def delete_replicas(content, dc: vim.Datacenter, vmgroup_name: str, to_delete: int, logger: logging.Logger) -> int:
    folder = get_obj(content, dc, [vim.Folder], vmgroup_name)
    if folder is None:
        raise ObjectNotFoundError(f'VM group "{vmgroup_name}" not found')

    vms = list()
    for vm in folder.childEntity:
        if isinstance(vm, vim.VirtualMachine):
            vms.append(vm)
    
    deleted = 0
    vm = None
    while deleted < to_delete:
        vm = vms.pop()
        logger.info(f'powering off VM "{vm.name}" in VM group "{vmgroup_name}"')
        try:
            vm.PowerOff()
        except vim.fault.VimFault as f:
            raise DestroyError(f'could not power off virtual machine "{vm}"": {str(f)}')

        logger.info(f'deleting VM "{vm.name}" from VM group "{vmgroup_name}"')
        try:
            vm.Destroy_Task()
            deleted += 1
        except vim.fault.VimFault as f:
            raise DestroyError(f'could not delete virtual machine "{vm}": {str(f)}')
        
    return deleted

def create_folder(dc: vim.Datacenter, folder_name: str):
    """
    create_folder creates a folder "folder_name" in datacenter "dc" under host_folder for the specified content
    """
    try:
        dc.vmFolder.CreateFolder(folder_name)
    except vim.fault.VimFault:
        raise ObjectAlreadyExists(f'Folder "{folder_name}" already exists')

def delete_folder(content, dc: vim.Datacenter, folder_name: str):
    """
    delete_folder deletes a folder "folder_name" in datacenter "dc" for the specified content
    """
    folder = get_obj(content, dc, [vim.Folder], folder_name)
    if folder is None:
        raise ObjectNotFoundError(f'Folder "{folder_name}" not found')
    
    # VMs have to be powered off
    for vm in folder.childEntity:
        if isinstance(vm, vim.VirtualMachine):
            try:
                vm.PowerOff()
            except vim.fault.VimFault as f:
                raise DestroyError(f'could not power off virtual machine "{vm}: {str(f)}"')

    # removes the folder and all children, i.e. VMs, from disk
    # give the power down tasks some time
    sleep(5)
    try:
        folder.Destroy_Task()
    except vim.fault.VimFault as f:
        raise DestroyError(f'could not delete folder "{folder_name}": {str(f)}')
    except vmodl.fault.ManagedObjectNotFound:
        pass

def vm_group_exists(content, dc: vim.Datacenter, vmgroup_name: str) -> bool:
    """
    vm_group_exists checks whether a given VM group "vmgroup_name" already exists in the inventory in datacenter "dc"
    """
    try:
        obj = get_obj(content, dc, [vim.Folder], vmgroup_name)
        if obj is None:
            return False
        else:
            return True
    except vmodl.fault.ManagedObjectNotFound:
        return False