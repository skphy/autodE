from autode.log import logger
from copy import deepcopy
from autode.config import Config
from autode.transition_states.base import TSbase
from autode.calculation import Calculation
from autode.mol_graphs import get_mapping_ts_template
from autode.mol_graphs import get_truncated_active_mol_graph
from autode.transition_states.templates import get_ts_templates
from autode.transition_states.templates import template_matches


def get_ts_guess_constrained_opt(reactant, method, keywords, name, distance_consts, product):
    """Get a TS guess from a constrained optimisation with the active atoms fixed at values defined in distance_consts

    Arguments:


    Returns:
       (autode.ts_guess.TSguess):
    """
    logger.info('Getting TS guess from constrained optimisation')

    opt_mol_with_const = deepcopy(reactant)
    const_opt = Calculation(name=f'{name}_constrained_opt', molecule=opt_mol_with_const, method=method,
                            keywords_list=keywords, n_cores=Config.n_cores, distance_constraints=distance_consts)
    const_opt.run()

    # Form a transition state guess from the optimised atoms and set the corresponding energy
    ts_guess = TSguess(atoms=const_opt.get_final_atoms(), reactant=reactant, product=product, name='ts_guess_const_opt')
    ts_guess.energy = const_opt.get_energy()

    return ts_guess


def get_template_ts_guess(reactant, product, bond_rearrangement,  method, keywords, dist_thresh=4.0):
    """Get a transition state guess object by searching though the stored TS templates

    Arguments:
        reactant (mol object): reactant object
        bond_rearrangement (list(tuple)):
        product (mol object): product object
        method (autode.wrappers.base.ElectronicStructureMethod):
        keywords (list(str)): Keywords to use for the ElectronicStructureMethod

    Keyword Arguments:
        dist_thresh (float): distance above which a constrained optimisation probably won't work due to the inital
                             geometry being too far away from the ideal (default: {4.0})

    Returns:
        TSGuess object: ts guess object
    """
    logger.info('Getting TS guess from stored TS template')
    active_bonds_and_dists_ts = {}

    # This will add edges so don't modify in place
    mol_graph = get_truncated_active_mol_graph(graph=reactant.graph, active_bonds=bond_rearrangement.all)
    ts_guess_templates = get_ts_templates()

    name = f'{reactant.name}_template_{bond_rearrangement}'

    for ts_template in ts_guess_templates:

        if template_matches(reactant=reactant, ts_template=ts_template, truncated_graph=mol_graph):
            mapping = get_mapping_ts_template(larger_graph=mol_graph, smaller_graph=ts_template.graph)
            for active_bond in bond_rearrangement.all:
                i, j = active_bond
                try:
                    active_bonds_and_dists_ts[active_bond] = ts_template.graph.edges[mapping[i],
                                                                                     mapping[j]]['distance']
                except KeyError:
                    logger.warning(f'Couldn\'t find a mapping for bond {i}-{j}')

            if len(active_bonds_and_dists_ts) == len(bond_rearrangement.all):
                logger.info('Found a TS guess from a template')

                if any([reactant.get_distance(*bond) > dist_thresh for bond in bond_rearrangement.all]):
                    logger.info(f'TS template has => 1 active bond distance larger than {dist_thresh}. Passing')
                    pass
                else:
                    return get_ts_guess_constrained_opt(reactant, method=method, keywords=keywords, name=name,
                                                        distance_consts=active_bonds_and_dists_ts, product=product)

    logger.info('Could not find a TS guess from a template')
    return None


class TSguess(TSbase):

    def __init__(self, atoms, reactant, product, name='ts_guess'):
        """
        Transition state guess

        Arguments:
            atoms (list(autode.atoms.Atom)):
            reactant (autode.complex.ReactantComplex):
            product (autode.complex.ProductComplex):

        Keyword Arguments:
            name (str): name of ts guess (default: {'ts_guess'})
        """
        super().__init__(name=name, atoms=atoms, reactant=reactant, product=product)


class SolvatedTSguess(TSguess):

    def __init__(self, atoms, reactant, product,  name='ts_guess'):
        super().__init__(atoms, reactant, product, name)

        self.qm_solvent_xyzs = None
        self.mm_solvent_xyzs = None

