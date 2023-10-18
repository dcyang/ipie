import numpy
import scipy.linalg

from ipie.thermal.estimators.fock import fock_matrix
from ipie.thermal.estimators.thermal import greens_function, one_rdm_stable, particle_number
from ipie.thermal.trial.chem_pot import compute_rho, find_chemical_potential
from ipie.thermal.trial.one_body import OneBody

class MeanField(OneBody):
    def __init__(self, hamiltonian, nelec, beta, dt, options={}, H1=None, verbose=False):
        OneBody.__init__(
            self, hamiltonian, nelec, beta, dt, options=options, H1=H1, verbose=verbose
        )
        if verbose:
            print(" # Building THF density matrix.")
        self.alpha = options.get("alpha", 0.75)
        self.max_scf_it = options.get("max_scf_it", self.max_it)
        self.max_macro_it = options.get("max_macro_it", self.max_it)
        self.find_mu = options.get("find_mu", True)
        self.P, HMF, self.mu = self.thermal_hartree_fock(hamiltonian, beta)
        muN = self.mu * numpy.eye(hamiltonian.nbasis, dtype=self.G.dtype)
        self.dmat = numpy.array(
            [
                scipy.linalg.expm(-dt * (HMF[0] - muN)),
                scipy.linalg.expm(-dt * (HMF[1] - muN)),
            ]
        )
        self.dmat_inv = numpy.array(
            [
                scipy.linalg.inv(self.dmat[0], check_finite=False),
                scipy.linalg.inv(self.dmat[1], check_finite=False),
            ]
        )
        self.G = numpy.array([greens_function(self.dmat[0]), greens_function(self.dmat[1])])
        self.nav = particle_number(self.P).real

    def thermal_hartree_fock(self, hamiltonian, beta):
        dt = self.dtau
        mu_old = self.mu
        P = self.P.copy()
        if self.verbose:
            print("# Determining Thermal Hartree--Fock Density Matrix.")
        for it in range(self.max_macro_it):
            if self.verbose:
                print(f"# Macro iteration: {it}")
            HMF = self.scf(hamiltonian, beta, mu_old, P)
            rho = numpy.array([scipy.linalg.expm(-dt * HMF[0]), scipy.linalg.expm(-dt * HMF[1])])
            if self.find_mu:
                mu = find_chemical_potential(
                    hamiltonian._alt_convention,
                    rho,
                    dt,
                    self.num_bins,
                    self.nav,
                    deps=self.deps,
                    max_it=self.max_it,
                    verbose=self.verbose,
                )
            else:
                mu = self.mu
            rho_mu = compute_rho(rho, mu_old, dt)
            P = one_rdm_stable(rho_mu, self.num_bins)
            dmu = abs(mu - mu_old)
            if self.verbose:
                print(f"# New mu: {mu:13.8e} Old mu: {mu_old:13.8e} Dmu: {dmu:13.8e}")
            if dmu < self.deps:
                break
            mu_old = mu
        return P, HMF, mu

    def scf(self, hamiltonian, beta, mu, P):
        # 1. Compute HMF
        HMF = fock_matrix(hamiltonian, P)
        dt = self.dtau
        muN = mu * numpy.eye(hamiltonian.nbasis, dtype=self.G.dtype)
        rho = numpy.array(
            [
                scipy.linalg.expm(-dt * (HMF[0] - muN)),
                scipy.linalg.expm(-dt * (HMF[1] - muN)),
            ]
        )
        Pold = one_rdm_stable(rho, self.num_bins)
        if self.verbose:
            print(" # Running Thermal SCF.")
        for it in range(self.max_scf_it):
            HMF = fock_matrix(hamiltonian, Pold)
            rho = numpy.array(
                [
                    scipy.linalg.expm(-dt * (HMF[0] - muN)),
                    scipy.linalg.expm(-dt * (HMF[1] - muN)),
                ]
            )
            Pnew = (1 - self.alpha) * one_rdm_stable(rho, self.num_bins) + self.alpha * Pold
            change = numpy.linalg.norm(Pnew - Pold)
            if change < self.deps:
                break
            Pold = Pnew.copy()
        if self.verbose:
            N = particle_number(P).real
            print(f" # Average particle number: {N:13.8e}")
        return HMF
