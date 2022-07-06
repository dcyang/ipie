import time
import numpy
from ipie.utils.misc import is_cupy
from ipie.estimators.local_energy_sd import exx_kernel_batch_real_rchol, ecoul_kernel_batch_real_rchol_uhf

def local_energy_single_det_uhf_batch_chunked(system, hamiltonian, walker_batch, trial):
    if is_cupy(trial.psi): # if even one array is a cupy array we should assume the rest is done with cupy
        import cupy
        assert(cupy.is_available())
        einsum = cupy.einsum
        zeros = cupy.zeros
        isrealobj = cupy.isrealobj
        zeros_like = cupy.zeros_like
    else:
        einsum = numpy.einsum
        zeros = numpy.zeros
        isrealobj = numpy.isrealobj
        zeros_like = numpy.zeros_like

    assert(hamiltonian.chunked)

    nwalkers = walker_batch.Ghalfa.shape[0]
    nalpha = walker_batch.Ghalfa.shape[1]
    nbeta = walker_batch.Ghalfb.shape[1]
    nbasis = hamiltonian.nbasis
    nchol = hamiltonian.nchol

    Ghalfa = walker_batch.Ghalfa.reshape(nwalkers, nalpha*nbasis)
    Ghalfb = walker_batch.Ghalfb.reshape(nwalkers, nbeta*nbasis)

    e1b = Ghalfa.dot(trial._rH1a.ravel())
    e1b += Ghalfb.dot(trial._rH1b.ravel())
    e1b += hamiltonian.ecore

    Ghalfa_send = Ghalfa.copy()
    Ghalfb_send = Ghalfb.copy()

    Ghalfa_recv = zeros_like(Ghalfa)
    Ghalfb_recv = zeros_like(Ghalfb)

    handler = walker_batch.mpi_handler
    senders = handler.senders
    receivers = handler.receivers

    rchola_chunk = trial._rchola_chunk
    rcholb_chunk = trial._rcholb_chunk

    Ghalfa = Ghalfa.reshape(nwalkers, nalpha*nbasis)
    Ghalfb = Ghalfb.reshape(nwalkers, nbeta*nbasis)
    ecoul_send = ecoul_kernel_batch_real_rchol_uhf(rchola_chunk, rcholb_chunk, Ghalfa, Ghalfb)
    Ghalfa = Ghalfa.reshape(nwalkers, nalpha, nbasis)
    Ghalfb = Ghalfb.reshape(nwalkers, nbeta, nbasis)
    exx_send = exx_kernel_batch_real_rchol(rchola_chunk, Ghalfa)
    exx_send += exx_kernel_batch_real_rchol(rcholb_chunk, Ghalfb)

    exx_recv = exx_send.copy()
    ecoul_recv = ecoul_send.copy()

    for icycle in range(handler.ssize-1):
        for isend, sender in enumerate(senders):
            if handler.srank == isend:
                handler.scomm.Send(Ghalfa_send,dest=receivers[isend], tag=1)
                handler.scomm.Send(Ghalfb_send,dest=receivers[isend], tag=2)
                handler.scomm.Send(ecoul_send,dest=receivers[isend], tag=3)
                handler.scomm.Send(exx_send,dest=receivers[isend], tag=4)
            elif handler.srank == receivers[isend]:
                sender = numpy.where(receivers == handler.srank)[0]
                handler.scomm.Recv(Ghalfa_recv,source=sender, tag=1)
                handler.scomm.Recv(Ghalfb_recv,source=sender, tag=2)
                handler.scomm.Recv(ecoul_recv,source=sender, tag=3)
                handler.scomm.Recv(exx_recv,source=sender, tag=4)
        handler.scomm.barrier()

        # prepare sending
        ecoul_send = ecoul_recv.copy()
        Ghalfa_recv = Ghalfa_recv.reshape(nwalkers, nalpha*nbasis)
        Ghalfb_recv = Ghalfb_recv.reshape(nwalkers, nbeta*nbasis)
        ecoul_send += ecoul_kernel_batch_real_rchol_uhf(rchola_chunk, rcholb_chunk, Ghalfa_recv, Ghalfb_recv)
        Ghalfa_recv = Ghalfa_recv.reshape(nwalkers, nalpha, nbasis)
        Ghalfb_recv = Ghalfb_recv.reshape(nwalkers, nbeta, nbasis)
        exx_send = exx_recv.copy()
        exx_send += exx_kernel_batch_real_rchol(rchola_chunk, Ghalfa_recv)
        exx_send += exx_kernel_batch_real_rchol(rcholb_chunk, Ghalfb_recv)
        Ghalfa_send = Ghalfa_recv.copy()
        Ghalfb_send = Ghalfb_recv.copy()

    if (len(senders)>1):
        for isend, sender in enumerate(senders):
            if handler.srank == sender: # sending 1 xshifted to 0 xshifted_buf
                handler.scomm.Send(ecoul_send,dest=receivers[isend], tag=1)
                handler.scomm.Send(exx_send,dest=receivers[isend], tag=2)
            elif handler.srank == receivers[isend]:
                sender = numpy.where(receivers == handler.srank)[0]
                handler.scomm.Recv(ecoul_recv,source=sender, tag=1)
                handler.scomm.Recv(exx_recv,source=sender, tag=2)

    e2b = ecoul_recv - exx_recv

    energy = zeros((nwalkers, 3), dtype=numpy.complex128)
    energy[:,0] = e1b+e2b
    energy[:,1] = e1b
    energy[:,2] = e2b

    return energy

def ecoul_kernel_batch_rchol_uhf_gpu(rchola_chunk, rcholb_chunk, Ghalfa, Ghalfb):
    import cupy
    einsum = cupy.einsum
    isrealobj = cupy.isrealobj

    if (isrealobj(rchola_chunk)):
        Xa = rchola_chunk.dot(Ghalfa.real.T) + 1.j * rchola_chunk.dot(Ghalfa.imag.T) # naux x nwalkers
        Xb = rcholb_chunk.dot(Ghalfb.real.T) + 1.j * rcholb_chunk.dot(Ghalfb.imag.T) # naux x nwalkers
    else:
        Xa = rchola_chunk.dot(Ghalfa.T)
        Xb = rcholb_chunk.dot(Ghalfb.T)

    ecoul = einsum("xw,xw->w", Xa, Xa, optimize=True)
    ecoul += einsum("xw,xw->w", Xb, Xb, optimize=True)
    ecoul += 2. * einsum("xw,xw->w", Xa, Xb, optimize=True)

    ecoul *= 0.5

    return ecoul

def exx_kernel_batch_rchol_gpu(rchola_chunk, Ghalfa):
    import cupy
    einsum = cupy.einsum
    nwalkers = Ghalfa.shape[0]
    nalpha = Ghalfa.shape[1]
    nbasis = Ghalfa.shape[2]
    nchol = rchola_chunk.shape[0]
    rchola_chunk = rchola_chunk.reshape(nchol, nalpha, nbasis)

    Txij = einsum("xim,wjm->wxji", rchola_chunk, Ghalfa)
    rchola_chunk = rchola_chunk.reshape(nchol, nalpha*nbasis)

    exx  = einsum("wxji,wxij->w",Txij,Txij)
    exx *= 0.5
    return exx


def local_energy_single_det_uhf_batch_chunked_gpu(system, hamiltonian, walker_batch, trial):
    import cupy
    assert(cupy.is_available())
    einsum = cupy.einsum
    zeros = cupy.zeros
    isrealobj = cupy.isrealobj
    zeros_like = cupy.zeros_like

    assert(hamiltonian.chunked)

    nwalkers = walker_batch.Ghalfa.shape[0]
    nalpha = walker_batch.Ghalfa.shape[1]
    nbeta = walker_batch.Ghalfb.shape[1]
    nbasis = hamiltonian.nbasis
    nchol = hamiltonian.nchol

    Ghalfa = walker_batch.Ghalfa.reshape(nwalkers, nalpha*nbasis)
    Ghalfb = walker_batch.Ghalfb.reshape(nwalkers, nbeta*nbasis)

    e1b = Ghalfa.dot(trial._rH1a.ravel())
    e1b += Ghalfb.dot(trial._rH1b.ravel())
    e1b += hamiltonian.ecore

    Ghalfa_send = Ghalfa.copy()
    Ghalfb_send = Ghalfb.copy()

    Ghalfa_recv = zeros_like(Ghalfa)
    Ghalfb_recv = zeros_like(Ghalfb)

    handler = walker_batch.mpi_handler
    senders = handler.senders
    receivers = handler.receivers

    rchola_chunk = trial._rchola_chunk
    rcholb_chunk = trial._rcholb_chunk

    Ghalfa = Ghalfa.reshape(nwalkers, nalpha*nbasis)
    Ghalfb = Ghalfb.reshape(nwalkers, nbeta*nbasis)
    ecoul_send = ecoul_kernel_batch_rchol_uhf_gpu(rchola_chunk, rcholb_chunk, Ghalfa, Ghalfb)
    Ghalfa = Ghalfa.reshape(nwalkers, nalpha, nbasis)
    Ghalfb = Ghalfb.reshape(nwalkers, nbeta, nbasis)
    exx_send = exx_kernel_batch_rchol_gpu(rchola_chunk, Ghalfa)
    exx_send += exx_kernel_batch_rchol_gpu(rcholb_chunk, Ghalfb)

    exx_recv = exx_send.copy()
    ecoul_recv = ecoul_send.copy()

    #print("ecoul (initial) = {} at {}".format(ecoul_recv, handler.rank))
    #print("exx (initial) = {} at {}".format(exx_recv, handler.rank))

    for icycle in range(handler.ssize-1):
        for isend, sender in enumerate(senders):
            if handler.srank == isend:
                Ghalfa_send = cupy.asnumpy(Ghalfa_send)
                Ghalfb_send = cupy.asnumpy(Ghalfb_send)
                ecoul_send = cupy.asnumpy(ecoul_send)
                exx_send = cupy.asnumpy(exx_send)
                handler.scomm.Send(Ghalfa_send,dest=receivers[isend], tag=1)
                handler.scomm.Send(Ghalfb_send,dest=receivers[isend], tag=2)
                handler.scomm.Send(ecoul_send,dest=receivers[isend], tag=3)
                handler.scomm.Send(exx_send,dest=receivers[isend], tag=4)
                Ghalfa_send = cupy.asarray(Ghalfa_send)
                Ghalfb_send = cupy.asarray(Ghalfb_send)
                ecoul_send = cupy.asarray(ecoul_send)
                exx_send = cupy.asarray(exx_send)
            elif handler.srank == receivers[isend]:
                sender = numpy.where(receivers == handler.srank)[0]
                Ghalfa_recv = cupy.asnumpy(Ghalfa_recv)
                Ghalfb_recv = cupy.asnumpy(Ghalfb_recv)
                ecoul_recv = cupy.asnumpy(ecoul_recv)
                exx_recv = cupy.asnumpy(exx_recv)
                handler.scomm.Recv(Ghalfa_recv,source=sender, tag=1)
                handler.scomm.Recv(Ghalfb_recv,source=sender, tag=2)
                handler.scomm.Recv(ecoul_recv,source=sender, tag=3)
                handler.scomm.Recv(exx_recv,source=sender, tag=4)
                Ghalfa_recv = cupy.asarray(Ghalfa_recv)
                Ghalfb_recv = cupy.asarray(Ghalfb_recv)
                ecoul_recv = cupy.asarray(ecoul_recv)
                exx_recv = cupy.asarray(exx_recv)
        handler.scomm.barrier()

        # prepare sending
        ecoul_send = ecoul_recv.copy()
        Ghalfa_recv = Ghalfa_recv.reshape(nwalkers, nalpha*nbasis)
        Ghalfb_recv = Ghalfb_recv.reshape(nwalkers, nbeta*nbasis)
        ecoul_send += ecoul_kernel_batch_rchol_uhf_gpu(rchola_chunk, rcholb_chunk, Ghalfa_recv, Ghalfb_recv)
        Ghalfa_recv = Ghalfa_recv.reshape(nwalkers, nalpha, nbasis)
        Ghalfb_recv = Ghalfb_recv.reshape(nwalkers, nbeta, nbasis)
        exx_send = exx_recv.copy()
        exx_send += exx_kernel_batch_rchol_gpu(rchola_chunk, Ghalfa_recv)
        exx_send += exx_kernel_batch_rchol_gpu(rcholb_chunk, Ghalfb_recv)
       # print("exx_recv = {} at {} in cylcle {}".format(exx_recv, handler.rank, icycle))
       # print("exx_send = {} at {} in cylcle {}".format(exx_send, handler.rank, icycle))
        Ghalfa_send = Ghalfa_recv.copy()
        Ghalfb_send = Ghalfb_recv.copy()

    if (len(senders)>1):
        for isend, sender in enumerate(senders):
            if handler.srank == sender: # sending 1 xshifted to 0 xshifted_buf
                ecoul_send = cupy.asnumpy(ecoul_send)
                exx_send = cupy.asnumpy(exx_send)
                handler.scomm.Send(ecoul_send,dest=receivers[isend], tag=1)
                handler.scomm.Send(exx_send,dest=receivers[isend], tag=2)
                ecoul_send = cupy.asarray(ecoul_send)
                exx_send = cupy.asarray(exx_send)
            elif handler.srank == receivers[isend]:
                sender = numpy.where(receivers == handler.srank)[0]
                ecoul_recv = cupy.asnumpy(ecoul_recv)
                exx_recv = cupy.asnumpy(exx_recv)
                handler.scomm.Recv(ecoul_recv,source=sender, tag=1)
                handler.scomm.Recv(exx_recv,source=sender, tag=2)
                ecoul_recv = cupy.asarray(ecoul_recv)
                exx_recv = cupy.asarray(exx_recv)

    handler.scomm.barrier()
    cupy.cuda.stream.get_current_stream().synchronize()

    e2b = ecoul_recv - exx_recv

    energy = zeros((nwalkers, 3), dtype=numpy.complex128)
    energy[:,0] = e1b+e2b
    energy[:,1] = e1b
    energy[:,2] = e2b


    cupy.cuda.stream.get_current_stream().synchronize()

    # print("ecoul = {} at {}".format(ecoul_recv, handler.rank))
    # print("exx = {} at {}".format(exx_recv, handler.rank))
    #print("energy = {} at {}".format(energy, handler.rank))
   # print("e1b = {} at {}".format(e1b, handler.rank))

    return energy