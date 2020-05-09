from copy import deepcopy
import numpy as np
from scipy.spatial import distance_matrix
from autode.solvent.explicit_solvent import do_explicit_solvent_qmmm
from autode.log import logger
from autode.geom import get_points_on_sphere
from autode.mol_graphs import union
from autode.species import Species
from autode.utils import requires_atoms
from autode.config import Config
from autode.conformers import Conformer


class Complex(Species):

    def get_atom_indexes(self, mol_index):
        """Get the first and last atom indexes of a molecule in a Complex"""
        assert mol_index < len(self.molecules)

        first_index = sum([mol.n_atoms for mol in self.molecules[:mol_index]])
        last_index = sum([mol.n_atoms for mol in self.molecules[:mol_index + 1]])

        return list(range(first_index, last_index))

    def _generate_conformers(self):
        """
        Generate rigid body conformers of a complex by (1) Fixing the first molecule, (2) initialising the second
        molecule's COM evenly on the points of a sphere around the first with a random rotation and (3) iterating
        until all molecules in the complex have been added
        """
        self.conformers = []
        n = 0

        # First molecule is static so get those atoms with the centroid at the origin
        first_mol_atoms = deepcopy(self.molecules[0].atoms)
        fist_mol_centroid = np.average(self.molecules[0].get_coordinates(), axis=0)

        for atom in first_mol_atoms:
            atom.coord -= fist_mol_centroid

        first_mol_coords = np.array([atom.coord for atom in first_mol_atoms])

        # TODO recursive call for > 2 molecules
        mol_centroid = np.average(self.molecules[1].get_coordinates(), axis=0)

        for _ in range(Config.num_complex_random_rotations):
            rotated_atoms = deepcopy(self.molecules[1].atoms)

            # Shift the molecule to the origin then rotate randomly
            theta, axis = np.random.uniform(0.0, 2.0*np.pi), np.random.uniform(-1.0, 1.0, size=3)
            for atom in rotated_atoms:
                atom.translate(vec=-mol_centroid)
                atom.rotate(axis, theta)

            # For every point generated on the surface on a unit sphere
            for point in get_points_on_sphere(n_points=Config.num_complex_sphere_points):
                shifted_atoms = deepcopy(rotated_atoms)

                far_enough_apart = False

                # Shift the molecule by 0.1 Å in the direction of the point (which has length 1) until the
                # minimum distance to the rest of the complex is 2.0 Å
                while not far_enough_apart:

                    for atom in shifted_atoms:
                        atom.coord += point * 0.1

                    mol_coords = np.array([atom.coord for atom in shifted_atoms])

                    if np.min(distance_matrix(first_mol_coords, mol_coords)) > 2.0:
                        far_enough_apart = True

                conformer = Conformer(name=f'{self.name}_conf{n}', atoms=first_mol_atoms+shifted_atoms,
                                      charge=self.charge, mult=self.mult)

                self.conformers.append(conformer)
                n += 1

        logger.info(f'Generated {n} conformers')
        return None

    @requires_atoms()
    def translate_mol(self, vec, mol_index):
        """
        Translate a molecule within a complex by a vector

        Arguments:
            vec (np.ndarray): Length 3 vector
            mol_index (int): Index of the molecule to translate. e.g. 2 will translate molecule 1 in the complex
                             they are indexed from 0

        """
        logger.info(f'Translating molecule {mol_index} by {vec} in {self.name}')

        for atom_index in self.get_atom_indexes(mol_index):
            self.atoms[atom_index].translate(vec)

        return None

    @requires_atoms()
    def rotate_mol(self, axis, theta, mol_index, origin=np.zeros(3)):
        """
        Rotate a molecule within a complex an angle theta about an axis given an origin

        Arguments:
            axis (np.ndarray): Length 3 vector
            theta (float): Length 3 vector
            origin (np.ndarray): Length 3 vector
            mol_index (int): Index of the molecule to translate. e.g. 2 will translate molecule 1 in the complex
                             they are indexed from 0

        """
        logger.info(f'Rotating molecule {mol_index} by {theta:.4f} radians in {self.name}')

        for atom_index in self.get_atom_indexes(mol_index):
            self.atoms[atom_index].translate(vec=-origin)
            self.atoms[atom_index].rotate(axis, theta)
            self.atoms[atom_index].translate(vec=origin)

        return None

    @requires_atoms()
    def calc_repulsion(self, mol_index):
        """Calculate the repulsion between a molecule and the rest of the complex"""

        coordinates = self.get_coordinates()

        mol_indexes = self.get_atom_indexes(mol_index)
        mol_coords = [coordinates[i] for i in mol_indexes]
        other_coords = [coordinates[i] for i in range(self.n_atoms) if i not in mol_indexes]

        # Repulsion is the sum over all pairs 1/r^4
        distance_mat = distance_matrix(mol_coords, other_coords)
        repulsion = 0.5 * np.sum(np.power(distance_mat, -4))

        return repulsion

    def __init__(self, *args, name='complex'):
        """
        Molecular complex e.g. VdW complex of one or more Molecules

        Arguments:
            *args (autode.species.Species):

        Keyword Arguments:
            name (str):
        """
        self.molecules = args
        self.molecule_atom_indexes = []

        # Calculate the overall charge and spin multiplicity on the system and initialise
        complex_charge = sum([mol.charge for mol in self.molecules])
        complex_mult = sum([mol.mult for mol in self.molecules]) - (len(self.molecules) - 1)

        complex_atoms = []
        for mol in self.molecules:
            complex_atoms += deepcopy(mol.atoms)

        super().__init__(name=name, atoms=complex_atoms, charge=complex_charge, mult=complex_mult)

        self.solvent = self.molecules[0].solvent                      # Solvent should be the same for all species
        self.graph = union(graphs=[mol.graph for mol in self.molecules])


class ReactantComplex(Complex):

    def run_const_opt(self, const_opt, method=None, n_cores=None):
        """Run a constrained optimisation using a const_opt calculation and set the new structure"""
        const_opt.run()

        self.energy = const_opt.get_energy()
        self.set_atoms(atoms=const_opt.get_final_atoms())

        return None


class ProductComplex(Complex):
    pass


class SolvatedReactantComplex(Complex):

    def run_const_opt(self, const_opt, method, n_cores):
        """Run a constrained optimisation of the ReactantComplex"""
        self.qm_solvent_atoms = None
        self.mm_solvent_atoms = None
        const_opt.molecule = deepcopy(self)
        const_opt.run()

        # Set the energy, new set of atoms then make the molecular graph
        self.set_atoms(atoms=const_opt.get_final_atoms())

        for i, charge in enumerate(const_opt.get_atomic_charges()):
            self.graph.nodes[i]['charge'] = charge

        energy, species_atoms, qm_solvent_atoms, mm_solvent_atoms = do_explicit_solvent_qmmm(self, method, n_confs=96, n_cores=n_cores)
        self.energy = energy
        self.set_atoms(species_atoms)
        self.qm_solvent_atoms = qm_solvent_atoms
        self.mm_solvent_atoms = mm_solvent_atoms

        return None

    def __init__(self, solvent_mol, *args, name='complex'):
        super().__init__(*args, name=name)
        self.solvent_mol = solvent_mol
        self.qm_solvent_atoms = None
        self.mm_solvent_atoms = None


class NCIComplex(Complex):
    pass


def get_complexes(reaction):
    """Creates Reactant and Product complexes for the reaction. If it is a SolvatedReaction,
    a SolvatedReactantComplex is returned"""

    if reaction.__class__.__name__ == 'SolvatedReaction':
        reac = SolvatedReactantComplex(reaction.solvent_mol, *reaction.reacs, name='r')
    else:
        reac = ReactantComplex(*reaction.reacs, name='r')
    prod = ProductComplex(*reaction.prods, name='p')
    return reac, prod
