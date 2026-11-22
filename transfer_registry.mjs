// Gateway-local cancellation registry. Each tracked operation must receive
// its AbortSignal and own a bounded process group. No job auto-retry/replay.
export class TransferRegistry {
  #active = new Set();

  async run(devices, toolName, operation) {
    if (!Array.isArray(devices) || devices.length===0 ||
        devices.some(x=>x!=='steamdeck' && x!=='windows') ||
        typeof toolName!=='string' || !toolName ||
        typeof operation !== 'function') throw new TypeError('Invalid transfer registration');
    const item={devices:new Set(devices),toolName,controller:new AbortController(),pending:null};
    this.#active.add(item);
    try {
      item.pending=Promise.resolve().then(()=>operation(item.controller.signal));
      return await item.pending;
    } finally {this.#active.delete(item);}
  }

  abortDevice(device) {
    if (device!=='steamdeck' && device!=='windows') throw new TypeError('Invalid device');
    let count=0;
    for(const entry of this.#active) if(entry.devices.has(device) && !entry.controller.signal.aborted) {
      entry.controller.abort();count++;
    }
    return count;
  }

  reconcile(policyCheck) {
    if(typeof policyCheck !== 'function')throw new TypeError('Missing policy check');
    let count=0;
    for(const entry of this.#active) {
      if(entry.controller.signal.aborted)continue;
      let denied=false;
      for(const device of entry.devices) {
        try {policyCheck(device,entry.toolName);}
        catch {denied=true;break;}
      }
      if(denied) {entry.controller.abort();count++;}
    }
    return count;
  }

  abortAll() {
    let count=0;
    for(const entry of this.#active) if(!entry.controller.signal.aborted) {
      entry.controller.abort();count++;
    }
    return count;
  }

  async drain() {
    const work=[...this.#active].map(x=>x.pending).filter(Boolean);
    await Promise.allSettled(work);
  }

  get activeCount() {return this.#active.size;}
}
