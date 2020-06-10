import numpy as np
import pandas as pd
from numba import njit, prange
from pathlib import Path
import joblib
import multiprocessing as mp
from itertools import product
import matplotlib.pyplot as plt
import h5py
import rc_params
rc_params.set_rc_params()

# conda install -c numba/label/dev numba

# conda install awkward
# conda install -c conda-forge pyarrow
import awkward

# N_Denmark = 535_806

do_fast_math = False
do_parallel_numba = False


def is_local_computer(N_local_cores=8):
    import platform
    if mp.cpu_count() <= N_local_cores and platform.system() == 'Darwin':
        return True
    else:
        return False


def get_cfg_default():
    cfg_default = dict(
                    # N_tot = 50_000 if is_local_computer() else 500_000, # Total number of nodes!
                    N_tot = 580_000, # Total number of nodes!
                    N_init = 100, # Initial Infected
                    N_ages = 1, # Number of age categories
                    mu = 40.0,  # Average number of connections of a node (init: 20)
                    sigma_mu = 0.0, # Spread (skewness) in N connections
                    beta = 0.01, # Daily infection rate (SIR, init: 0-1, but beta = (2mu/N_tot)* betaSIR)
                    sigma_beta = 0.0, # Spread in rates, beta
                    rho = 0.0, # Spacial dependency. Average distance to connect with.
                    lambda_E = 1.0, # E->I, Lambda(from E states)
                    lambda_I = 1.0, # I->R, Lambda(from I states)
                    epsilon_rho = 0.01, # fraction of connections not depending on distance
                    beta_scaling = 1.0, # anmunt of beta scaling
                    age_mixing = 1.0,
                    algo = 2, # node connection algorithm
                    )
    return cfg_default

sim_pars_ints = ('N_tot', 'N_init',  'N_ages', 'algo')


def filename_to_ID(filename):
    return int(filename.split('ID__')[1].strip('.csv'))


class DotDict(dict):
    """
    Class that allows a dict to indexed using dot-notation.
    Example:
    >>> dotdict = DotDict({'first_name': 'Christian', 'last_name': 'Michelsen'})
    >>> dotdict.last_name
    'Michelsen'
    """

    def __getattr__(self, item):
        if item in self:
            return self.get(item)
        raise KeyError(f"'{item}' not in dict")

    def __setattr__(self, key, value):
        if key in self:
            self[key] = value
            return
        raise KeyError(
            "Only allowed to change existing keys with dot notation. Use brackets instead."
        )


def filename_to_dotdict(filename, normal_string=False, animation=False):
    return DotDict(filename_to_dict(filename, normal_string=normal_string, animation=animation))


def get_num_cores(num_cores_max):
    num_cores = mp.cpu_count() - 1
    if num_cores >= num_cores_max:
        num_cores = num_cores_max
    return num_cores


def dict_to_filename_with_dir(cfg, ID):
    filename = Path('Data') / 'ABN' 
    file_string = ''
    for key, val in cfg.items():
        file_string += f"{key}__{val}__"
    file_string = file_string[:-2] # remove trailing _
    filename = filename / file_string
    file_string += f"__ID__{ID:03d}.csv"
    filename = filename / file_string
    return str(filename)


def filename_to_dict(filename, normal_string=False, animation=False): # , 
    cfg = {}

    if normal_string:
        keyvals = filename.split('__')
    elif animation:
        keyvals = filename.split('/')[-1].split('.animation')[0].split('__')
    else:
        keyvals = str(Path(filename).stem).split('__')

    keyvals_chunks = [keyvals[i:i + 2] for i in range(0, len(keyvals), 2)]
    for key, val in keyvals_chunks:
        if not key == 'ID':
            if key in sim_pars_ints:
                cfg[key] = int(val)
            else:
                cfg[key] = float(val)
    return DotDict(cfg)


def generate_filenames(d_sim_pars, N_loops=10, force_overwrite=False):
    filenames = []

    nameval_to_str = [[f'{name}__{x}' for x in lst] for (name, lst) in d_sim_pars.items()]
    all_combinations = list(product(*nameval_to_str))

    cfg = get_cfg_default()
    # combination = all_combinations[0]
    for combination in all_combinations:
        for s in combination:
            name, val = s.split('__')
            val = int(val) if name in sim_pars_ints else float(val)
            cfg[name] = val

        # ID = 0
        for ID in range(N_loops):
            filename = dict_to_filename_with_dir(cfg, ID)

            not_existing = (not Path(filename).exists())
            if not_existing or force_overwrite: 
                filenames.append(filename)
    return filenames
    

def get_num_cores_N_tot_specific(d_simulation_parameters, num_cores_max):
    num_cores = get_num_cores(num_cores_max)

    if isinstance(d_simulation_parameters, dict) and 'N_tot' in d_simulation_parameters.keys():
        N_tot_max = max(d_simulation_parameters['N_tot'])
        if 500_000 < N_tot_max <= 1_000_000:
            num_cores = 25
        elif 1_000_000 < N_tot_max <= 2_000_000:
            num_cores = 15
        elif 2_000_000 < N_tot_max <= 5_000_000:
            num_cores = 8
        elif 5_000_000 < N_tot_max:
            num_cores = 6

    if num_cores > num_cores_max:
        num_cores = num_cores_max
    
    return num_cores


@njit
def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = np.radians(lon1), np.radians(lat1), np.radians(lon2), np.radians(lat2)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat/2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2.0)**2
    return 6367 * 2 * np.arcsin(np.sqrt(a)) # [km]


#%%


@njit
def initialize_connections_and_rates(N_tot, sigma_mu, beta, sigma_beta, beta_scaling):

    connection_weight = np.ones(N_tot, dtype=np.float32)
    infection_weight = np.ones(N_tot, dtype=np.float32)

    for i in range(N_tot): # prange
        if (np.random.rand() < sigma_mu):
            connection_weight[i] = 0.1 - np.log(np.random.rand())# / 1.0
        else:
            connection_weight[i] = 1.1

        if (np.random.rand() < sigma_beta):
            infection_weight[i] = -np.log(np.random.rand())*beta
        else:
            infection_weight[i] = beta
        
        f = 1 / (beta_scaling + 1)
        ra_R0_change = np.random.rand()
        if ra_R0_change < f:
            infection_weight[i] = infection_weight[i]*beta_scaling
        else:
            infection_weight[i] = infection_weight[i]/beta_scaling

    return connection_weight, infection_weight


@njit
def initialize_nested_lists(N, dtype):
    nested_list = List()
    for i in range(N):
        tmp = List()
        tmp.append(dtype(-1))
        nested_list.append(tmp)
        nested_list[-1].pop(0) # trick to tell compiler which dtype
    return nested_list#, is_first_value


import numpy as np
import numba as nb
from numba import njit
from numba.typed import List

@njit
def initialize_ages(N_tot, N_ages, connection_weight):

    ages = -1*np.ones(N_tot, dtype=np.int8)
    ages_total_counts = np.zeros(N_ages, dtype=np.uint32)
    # ages_in_state = -1*np.ones((N_ages, N_tot), dtype=np.int32) # XXX nested
    ages_in_state = initialize_nested_lists(N_ages, dtype=np.int32) # XXX nested

    for idx in range(N_tot): # prange
        age = np.random.randint(N_ages)
        ages[idx] = age
        # ages_in_state[age, ages_total_counts[age]] = idx # XXX nested
        ages_total_counts[age] += 1
        ages_in_state[age].append(np.int32(idx))


    PT_ages = np.zeros(N_ages, dtype=np.float32)
    PC_ages = List()
    PP_ages = List()
    for i_age_group in range(N_ages): # prange
        indices = np.asarray(ages_in_state[i_age_group]) # , :ages_total_counts[i_age_group]] # XXX nested
        # indices = ages_in_state[i_age_group] # , :ages_total_counts[i_age_group]] # XXX nested
        connection_weight_ages = connection_weight[indices]
        PT_age = np.sum(connection_weight_ages)
        PC_age = np.cumsum(connection_weight_ages)
        PP_age = PC_age / PT_age

        PT_ages[i_age_group] = PT_age
        PC_ages.append(PC_age)
        PP_ages.append(PP_age)

    return ages, ages_total_counts, ages_in_state, PT_ages, PC_ages, PP_ages


@njit
def update_node_connections(N_connections, individual_rates, which_connections, which_connections_reference, coordinates, infection_weight, N_connections_reference, rho_tmp, rho_scale, continue_run, id1, id2):

    #  Make sure no element is present twice
    accept = True
    for idn in range(N_connections[id1]):  # prange
        # if which_connections[id1, idn] == id2:
        if which_connections[id1][idn] == id2: # XXX
            accept = False

    # if (N_connections[id1] < N_AK_MAX) and (N_connections[id2] < N_AK_MAX) and (id1 != id2) and accept:
    if (id1 != id2) and accept:
        r = haversine(coordinates[id1, 0], coordinates[id1, 1], coordinates[id2, 0], coordinates[id2, 1])
        if np.exp(-r*rho_tmp/rho_scale) > np.random.rand():
            
            # individual_rates[id1, N_connections[id1]] = infection_weight[id1] # XXX
            individual_rates[id1].append(infection_weight[id1]) # XXXX id1 -> id2
            # individual_rates[id2, N_connections[id2]] = infection_weight[id2] # Changed from id1 # XXX
            individual_rates[id2].append(infection_weight[id2]) # Changed from id1

            # which_connections[id1, N_connections[id1]] = id2 # XXX
            which_connections[id1].append(id2) # XXX
            # which_connections_reference[id1, N_connections[id1]] = id2                        
            which_connections_reference[id1].append(id2)
            # which_connections[id2, N_connections[id2]] = id1
            which_connections[id2].append(id1) # XXX
            # which_connections_reference[id2, N_connections[id2]] = id1
            which_connections_reference[id2].append(id1)

            N_connections[id1] += 1 
            N_connections[id2] += 1
            N_connections_reference[id1] += 1 
            N_connections_reference[id2] += 1
            continue_run = False

    return continue_run


@njit
def run_algo_2(PP_ages, m_i, m_j, N_connections, individual_rates, which_connections, which_connections_reference, coordinates, infection_weight, N_connections_reference, rho_tmp, rho_scale, ages_in_state):


    continue_run = True
    while continue_run:
        
        id1 = np.searchsorted(PP_ages[m_i], np.random.rand())
        id2 = np.searchsorted(PP_ages[m_j], np.random.rand())

        id1 = ages_in_state[m_i][id1]
        id2 = ages_in_state[m_j][id2]
        
        continue_run = update_node_connections(N_connections, individual_rates, which_connections, which_connections_reference, coordinates, infection_weight, N_connections_reference, rho_tmp, rho_scale, continue_run, id1, id2)


@njit
def run_algo_1(PP_ages, m_i, m_j, N_connections, individual_rates, which_connections, which_connections_reference, coordinates, infection_weight, N_connections_reference, rho_tmp, rho_scale, ages_in_state):

    ra1 = np.random.rand()
    id1 = np.searchsorted(PP_ages[m_i], ra1) 
    id1 = ages_in_state[m_i][id1]

    N_algo_1_tries = 0

    continue_run = True
    while continue_run:
        ra2 = np.random.rand()          
        id2 = np.searchsorted(PP_ages[m_j], ra2)
        id2 = ages_in_state[m_j][id2]

        N_algo_1_tries += 1
        rho_tmp *= 0.9995 

        continue_run = update_node_connections(N_connections, individual_rates, which_connections, which_connections_reference, coordinates, infection_weight, N_connections_reference, rho_tmp, rho_scale, continue_run, id1, id2)
    
    return N_algo_1_tries


@njit
def connect_nodes(mu, epsilon_rho, rho, algo, PP_ages, N_connections, individual_rates, which_connections, which_connections_reference, coordinates, infection_weight, N_connections_reference, rho_scale, N_ages, age_matrix, ages_in_state, verbose):

    num_prints = 0

    for m_i in range(N_ages):
        for m_j in range(N_ages):
            for counter in range(int(age_matrix[m_i, m_j])): 

                if np.random.rand() > epsilon_rho:
                    rho_tmp = rho
                else:
                    rho_tmp = 0.0

                # PP_i, PP_j = PP_ages[0], PP_ages[1]
                if (algo == 2):
                    run_algo_2(PP_ages, m_i, m_j, N_connections, individual_rates, which_connections, which_connections_reference, coordinates, infection_weight, N_connections_reference, rho_tmp, rho_scale, ages_in_state)

                else:
                    N_algo_1_tries = run_algo_1(PP_ages, m_i, m_j, N_connections, individual_rates, which_connections, which_connections_reference, coordinates, infection_weight, N_connections_reference, rho_tmp, rho_scale, ages_in_state)

                    if verbose and num_prints < 10:
                        # print(N_algo_1_tries, num_prints)
                        num_prints += 1


@njit
def make_initial_infections(N_init, which_state, state_total_counts, agents_in_state, csMov, N_connections_reference, which_connections, which_connections_reference, N_connections, individual_rates, SIR_transition_rates, ages_in_state, initial_ages_exposed):

    TotMov = 0.0

    # XXX nested
    possible_idxs = List()
    for age_exposed in initial_ages_exposed:
        for agent in ages_in_state[age_exposed]:
            possible_idxs.append(agent)

    # possible_idxs = ages_in_state[initial_ages_exposed].flatten() # XXX nested
    # possible_idxs = possible_idxs[possible_idxs != -1]

    ##  Now make initial infections
    random_indices = np.random.choice(np.asarray(possible_idxs), size=N_init, replace=False)
    # idx = random_indices[0]
    for idx in random_indices:
        new_state = np.random.randint(0, 4)
        which_state[idx] = new_state

        # agents_in_state[new_state, state_total_counts[new_state]] = idx
        agents_in_state[new_state].append(idx)
        state_total_counts[new_state] += 1  
        TotMov += SIR_transition_rates[new_state]
        csMov[new_state:] += SIR_transition_rates[new_state]
        for i1 in range(N_connections_reference[idx]):
            # Af = which_connections_reference[idx, i1] # XXX
            Af = which_connections_reference[idx][i1]
            for i2 in range(N_connections[Af]):
                # if which_connections[Af, i2] == idx: # XXX
                if which_connections[Af][i2] == idx:
                    # XXX maybe pop?
                    which_connections[Af].pop(i2) 
                    individual_rates[Af].pop(i2)
                    # for i3 in range(i2, N_connections[Af]):
                    #     # which_connections[Af, i3] = which_connections[Af, i3+1] 
                    #     # which_connections[Af][i3] = which_connections[Af][i3+1] # XXX
                    #     individual_rates[Af, i3] = individual_rates[Af, i3+1]
                    N_connections[Af] -= 1
                    break 
    return TotMov


# @njit
# def foo():
#     d = dict()
#     d[3] = []
#     return d
# d = foo()


#%%

@njit
def run_simulation(N_tot, TotMov, csMov, state_total_counts, agents_in_state, which_state, csInf, N_states, InfRat, SIR_transition_rates, infectious_state, N_connections, individual_rates, N_connections_reference, which_connections_reference, which_connections, ages, H_probability_matrix_csum, H_which_state, H_agents_in_state, H_state_total_counts, H_move_matrix_sum, H_cumsum_move, H_move_matrix_cumsum, mu, tau_I, N_contacts_remove, nts, verbose):

    out_time = List()
    out_state_counts = List()
    out_which_state = List()
    out_N_connections = List()
    out_H_state_total_counts = List()

    out_which_connections = which_connections.copy() 
    out_individual_rates = individual_rates.copy()
    out_ages = ages.copy()


    daily_counter = 0

    Tot = 0.0
    TotInf = 0.0
    click = 0 
    counter = 0
    Csum = 0.0 
    RT = 0.0

    H_tot_move = 0
    H_counter = 0

    intervention_switch = False
    has_printed = False
    closed_contacts = initialize_nested_lists(N_tot, dtype=np.int32) 
    closed_contacts_rate = initialize_nested_lists(N_tot, dtype=np.float32) 

    # Run the simulation ################################
    continue_run = True
    while continue_run:

        s = 0

        counter += 1 
        Tot = TotMov + TotInf + H_tot_move
        dt = - np.log(np.random.rand()) / Tot    
        RT = RT + dt
        Csum = 0.0
        ra1 = np.random.rand()


        # # removal of contacts
        # I_tot = np.sum(state_total_counts[4:8])

        # if tau_I < I_tot / N_tot and not intervention_switch:
        #     intervention_switch = True

        #     for c in range(int(N_tot*mu*N_contacts_remove)):

        #         idx1 = np.random.randint(N_tot)

        #         N_contacts_idx = len(which_connections[idx1])
        #         if N_contacts_idx > 0:
        #             idn1 = np.random.randint(N_contacts_idx)

        #             closed_contacts[idx1].append(idn1)
        #             rates1 = individual_rates[idx1]
        #             closed_contacts_rate[idx1].append(rates1[idn1])
        #             TotInf -= rates1[idn1]
        #             csInf[which_state[idx1]:] -= rates1[idn1]
        #             rates1[idn1] = 0

        #             idx2 = which_connections[idx1][idn1]
        #             connections2 = which_connections[idx2]
        #             idn2 = -1
        #             for i in range(len(connections2)):
        #                 if connections2[i] == idx1:
        #                     idn2 = i 
        #             if idn2 < 0:
        #                 print(idx1, idn1, idx2, idn2)


        #             closed_contacts[idx2].append(idn2)
        #             rates2 = individual_rates[idx2]
        #             try:
        #                 closed_contacts_rate[idx2].append(rates2[idn2])
        #             except:
        #                 if not has_printed:
        #                     print("closed_contacts_rate")
        #                     print(idx1, idn1, idx2, idn2)
        #                     print(rates2)
        #                     print(closed_contacts_rate[idx2])
        #                     has_printed = True

        #             TotInf -= rates2[idn2]
        #             csInf[which_state[idx2]:] -= rates2[idn2]
        #             rates2[idn2] = 0

        #                 #  print(c, idx1, idn1, idx2, idn2)


        #######/ Here we move infected between states
        AC = 0 
        if TotMov / Tot > ra1:
            s = 1
            x = csMov / Tot
            i1 = np.searchsorted(x, ra1)
            Csum = csMov[i1] / Tot
            for i2 in range(state_total_counts[i1]):
                Csum += SIR_transition_rates[i1] / Tot
                if Csum > ra1:
                    # idx = agents_in_state[i1, i2] # XXX
                    idx = agents_in_state[i1][i2]
                    AC = 1
                    break                
            
            # We have chosen idx to move -> here we move it
            # agents_in_state[i1+1, state_total_counts[i1+1]] = idx # XXX
            agents_in_state[i1+1].append(idx)
            agents_in_state[i1].pop(i2) 
            # for j in range(i2, state_total_counts[i1]):
            #     # agents_in_state[i1, j] = agents_in_state[i1, j+1] # XXX

            which_state[idx] += 1
            state_total_counts[i1] -= 1 
            state_total_counts[i1+1] += 1      
            TotMov -= SIR_transition_rates[i1] 
            TotMov += SIR_transition_rates[i1+1]     
            csMov[i1] -= SIR_transition_rates[i1]
            csMov[i1+1:N_states] += SIR_transition_rates[i1+1]-SIR_transition_rates[i1]
            csInf[i1] -= InfRat[idx]

            # Moves TO infectious State from non-infectious
            if which_state[idx] == infectious_state: 
                for i1 in range(N_connections[idx]): # Loop over row idx	  
                    # if which_state[which_connections[idx, i1]] < 0: # XXX
                    if which_state[which_connections[idx][i1]] < 0:
                        # TotInf += individual_rates[idx, i1] # XXX
                        TotInf += individual_rates[idx][i1]
                        # InfRat[idx] += individual_rates[idx, i1] # XXX
                        InfRat[idx] += individual_rates[idx][i1]
                        # csInf[which_state[idx]:N_states] += individual_rates[idx, i1] # XXX
                        csInf[which_state[idx]:N_states] += individual_rates[idx][i1]
           
            # If this moves to Recovered state
            if which_state[idx] == N_states-1:
                for i1 in range(N_connections[idx]): # Loop over row idx
                    # TotInf -= individual_rates[idx, i1] # XXX
                    TotInf -= individual_rates[idx][i1] 
                    # InfRat[idx] -= individual_rates[idx, i1] # XXX
                    InfRat[idx] -= individual_rates[idx][i1]
                    # csInf[which_state[idx]:N_states] -= individual_rates[idx, i1] # XXX
                    csInf[which_state[idx]:N_states] -= individual_rates[idx][i1]


                # XXX HOSPITAL
                # Now in hospital track
                H_state = np.searchsorted(H_probability_matrix_csum[ages[idx]], np.random.rand())

                H_which_state[idx] = H_state
                # H_agents_in_state[H_state, H_state_total_counts[H_state]] = idx # XXX
                H_agents_in_state[H_state].append(idx)
                H_state_total_counts[H_state] += 1
                
                H_tot_move += H_move_matrix_sum[H_state, ages[idx]]
                H_cumsum_move[H_state:] += H_move_matrix_sum[H_state, ages[idx]] 


                # if H_counter < 100:
                #     print(idx, "jumps into hospital track", H_state, H_agents_in_state[H_state])
                #     H_counter += 1


        # Here we infect new states
        elif (TotMov + TotInf) / Tot > ra1:  # XXX HOSPITAL
        # else: # XXX HOSPITAL
            s = 2
            x = TotMov/Tot + csInf/Tot
            i1 = np.searchsorted(x, ra1)
            Csum = TotMov/Tot + csInf[i1]/Tot
            for i2 in range(state_total_counts[i1]):
                # idy = agents_in_state[i1, i2] # XXX
                idy = agents_in_state[i1][i2]
                for i3 in range(N_connections[idy]): 
                    Csum += individual_rates[idy][i3] / Tot
                    if Csum > ra1:
                        # idx = which_connections[idy, i3]  # XXX    
                        idx = which_connections[idy][i3] 
                        which_state[idx] = 0 
                        # agents_in_state[0, state_total_counts[0]] = idx	#
                        agents_in_state[0].append(idx)
                        state_total_counts[0] += 1
                        TotMov += SIR_transition_rates[0]	      
                        csMov += SIR_transition_rates[0]
                        AC = 1
                        break                    
                if AC == 1:
                    break

            # Here we update infection lists      
            for i1 in range(N_connections_reference[idx]):
                # Af = which_connections_reference[idx, i1] # XXX
                Af = which_connections_reference[idx][i1]
                for i2 in range(N_connections[Af]):
                    # if which_connections[Af, i2] == idx: # XXX
                    if which_connections[Af][i2] == idx:
                        if (which_state[Af] >= infectious_state) and (which_state[Af] < N_states-1):	      
                            # TotInf -= individual_rates[Af, i2] # XXX
                            TotInf -= individual_rates[Af][i2]
                            # InfRat[Af] -= individual_rates[Af, i2] # XXX
                            InfRat[Af] -= individual_rates[Af][i2] 
                            # csInf[which_state[Af]:N_states] -= individual_rates[Af, i2] # XXX
                            csInf[which_state[Af]:N_states] -= individual_rates[Af][i2]
                        
                        which_connections[Af].pop(i2)
                        individual_rates[Af].pop(i2)
                        # for i3 in range(i2, N_connections[Af]):
                        #     # which_connections[Af, i3] = which_connections[Af, i3+1] # XXX
                        #     individual_rates[Af, i3] = individual_rates[Af, i3+1]
                        N_connections[Af] -= 1 
                        break


        ## move between hospital tracks
        else:
            s = 3
            x = (TotMov + TotInf + H_cumsum_move) / Tot
            H_old_state = np.searchsorted(x, ra1)
            Csum = (TotMov + TotInf + H_cumsum_move[H_old_state]) / Tot
            for idx_H_state in range(len(H_agents_in_state[H_old_state])):
                
                idx = H_agents_in_state[H_old_state][idx_H_state]
                Csum += H_move_matrix_sum[H_old_state, ages[idx]] / Tot
                
                if Csum > ra1:
                    # idx = agents_in_state[H_old_state, idx]
                    

                    AC = 1
                    H_ra = np.random.rand()

                    H_tmp = H_move_matrix_cumsum[H_which_state[idx], :, ages[idx]] / H_move_matrix_sum[H_which_state[idx], ages[idx]]
                    H_new_state = np.searchsorted(H_tmp, H_ra)

                    # for nested list pop element
                    # We have chosen idx to move -> here we move it
                    H_agents_in_state[H_old_state].pop(idx_H_state)
                    # for h in range(idx, H_state_total_counts[H_old_state]):
                    #     H_agents_in_state[H_old_state, h] = H_agents_in_state[H_old_state, h+1] 

                    H_which_state[idx] = H_new_state
                    # H_agents_in_state[H_new_state, H_state_total_counts[H_new_state]] = idx # XXX
                    H_agents_in_state[H_new_state].append(idx)
                    H_state_total_counts[H_old_state] -= 1
                    H_state_total_counts[H_new_state] += 1

                    H_tot_move += H_move_matrix_sum[H_new_state, ages[idx]] - H_move_matrix_sum[H_old_state, ages[idx]]

                    # moving forward
                    if H_old_state < H_new_state:
                        H_cumsum_move[H_old_state:H_new_state] -= H_move_matrix_sum[H_old_state, ages[idx]] 
                        H_cumsum_move[H_new_state:] += H_move_matrix_sum[H_new_state, ages[idx]] - H_move_matrix_sum[H_old_state, ages[idx]] 
                    
                    #moving backwards
                    else:
                        H_cumsum_move[H_new_state:H_old_state] += H_move_matrix_sum[H_old_state, ages[idx]] 
                        H_cumsum_move[H_new_state:] += H_move_matrix_sum[H_new_state, ages[idx]] - H_move_matrix_sum[H_old_state, ages[idx]] 

                    break


        ################

        if nts*click < RT:

            daily_counter += 1
            out_time.append(RT)
            out_state_counts.append(state_total_counts.copy())
            out_H_state_total_counts.append(H_state_total_counts.copy())
            # H_state_total_counts # TODO

            if daily_counter >= 10:
                daily_counter = 0

                out_which_state.append(which_state.copy())
                out_N_connections.append(N_connections.copy())

            click += 1 

        # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 
        # # # # # # # # # # # BUG CHECK  # # # # # # # # # # # # # # # # # # # # # # # #
        # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 

        continue_run, TotMov, TotInf = do_bug_check(counter, continue_run, TotInf, TotMov, verbose, state_total_counts, N_states, N_tot, AC, csMov, ra1, s, idx_H_state, H_old_state, H_agents_in_state, x)

    return out_time, out_state_counts, out_which_state, out_N_connections, out_which_connections, out_individual_rates, out_H_state_total_counts, out_ages

#%%

@njit
def do_bug_check(counter, continue_run, TotInf, TotMov, verbose, state_total_counts, N_states, N_tot, AC, csMov, ra1, s, idx_H_state, H_old_state, H_agents_in_state, x):

    if counter > 100_000_000:
        # if verbose:
        print("counter > 100_000_000")
        continue_run = False
    
    if (TotInf + TotMov < 0.0001) and (TotMov + TotInf > -0.00001): 
        continue_run = False
        if verbose:
            print("Equilibrium")
    
    if state_total_counts[N_states-1] > N_tot-10:      
        if verbose:
            print("2/3 through")
        continue_run = False

    # Check for bugs
    if AC == 0: 
        print("No Chosen rate", csMov, ra1, s, idx_H_state, H_old_state, H_agents_in_state[H_old_state], x)
        continue_run = False
    
    if (TotMov < 0) and (TotMov > -0.001):
        TotMov = 0 
        
    if (TotInf < 0) and (TotInf > -0.001):
        TotInf = 0 
        
    if (TotMov < 0) or (TotInf < 0): 
        print("\nNegative Problem", TotMov, TotInf)
        continue_run = False

    return continue_run, TotMov, TotInf



from numba.typed import List
import time

@njit
def get_size_gb(x):
    return x.size * x.itemsize / 10**9


#%%

@njit
def numba_cumsum_2D(x, axis):
    y = np.zeros_like(x)
    n, m = np.shape(x)
    if axis==1:
        for i in range(n):
            y[i, :] = np.cumsum(x[i, :])
    elif axis==0:
        for j in range(m):
            y[:, j] = np.cumsum(x[:, j])
    return y

@njit
def numba_cumsum_3D(x, axis):
    y = np.zeros_like(x)
    n, m, p = np.shape(x)

    if axis==2:
        for i in range(n):
            for j in range(m):
                y[i, j, :] = np.cumsum(x[i, j, :])
    elif axis==1:
        for i in range(n):
            for k in range(p):
                y[i, :, k] = np.cumsum(x[i, :, k])
    elif axis==0:
        for j in range(m):
            for k in range(p):
                y[:, j, k] = np.cumsum(x[:, j, k])
    return y

from numba import generated_jit, types

# overload
# https://jcristharif.com/numba-overload.html

@generated_jit(nopython=True)
def numba_cumsum_shape(x, axis):
    if x.ndim == 1:
        return lambda x, axis: np.cumsum(x)
    elif x.ndim == 2:
        return lambda x, axis: numba_cumsum_2D(x, axis=axis)
    elif x.ndim == 3:
        return lambda x, axis: numba_cumsum_3D(x, axis=axis)

@njit
def numba_cumsum(x, axis=None):
    if axis is None and x.ndim != 1:
        print("numba_cumsum was used without any axis keyword set. Continuing using axis=0.")
        axis = 0
    return numba_cumsum_shape(x, axis)


@njit
def calculate_epsilon(alpha_age, N_ages):
    return 1 / N_ages * alpha_age

@njit
def calculate_age_proportions_1D(alpha_age, N_ages):
    epsilon = calculate_epsilon(alpha_age, N_ages)
    x = epsilon * np.ones(N_ages, dtype=np.float32) 
    x[0] = 1-x[1:].sum()
    return x


@njit
def calculate_age_proportions_2D(alpha_age, N_ages):
    epsilon = calculate_epsilon(alpha_age, N_ages)
    A = epsilon * np.ones((N_ages, N_ages), dtype=np.float32) 
    for i in range(N_ages):
            A[i, i] = 1-np.sum(np.delete(A[i, :], i))
    return A


#%%

# @njit
@njit
def single_run_numba(N_tot, N_init, N_ages, mu, sigma_mu, beta, sigma_beta, rho, lambda_E, lambda_I, algo, epsilon_rho, beta_scaling, age_mixing, ID, coordinates, verbose=False):
    
    # N_tot = 50_000 # Total number of nodes!
    # N_init = 100 # Initial Infected
    # N_ages = 10 #
    # mu = 40.0  # Average number of connections of a node (init: 20)
    # sigma_mu = 1.0 # Spread (skewness) in N connections
    # beta = 0.01 # Daily infection rate (SIR, init: 0-1, but beta = (2mu/N_tot)* betaSIR)
    # sigma_beta = 0.0 # Spread in rates, beta (beta_eff = beta - sigma_beta+2*sigma_beta*rand[0,1])... could be exponential?
    # rho = 0 # Spacial dependency. Average distance to connect with.
    # lambda_E = 1.0 # E->I, Lambda(from E states)
    # lambda_I = 1.0 # I->R, Lambda(from I states)
    # algo = 1 # node connection algorithm
    # epsilon_rho = 0.01 # fraction of connections not depending on distance
    # beta_scaling = 1.0 # 0: as normal, 1: half of all (beta)rates are set to 0 the other half doubled
    # age_mixing = 1.0
    # ID = 0
    # coordinates = np.load('Data/GPS_coordinates.npy')[:N_tot]
    # verbose = True

    tau_I = 0.001 # 0.1 percent of N_tot infected
    N_contacts_remove = 0.05 # percent

    np.random.seed(ID)

    nts = 0.1 # Time step (0.1 - ten times a day)
    N_states = 9 # number of states
    rho_scale = 1000 # scale factor of rho
    initial_ages_exposed = np.arange(N_ages)
    mu /= 2 # fix to factor in that both nodes have connections with each other
 
    # For generating Network
    # which_connections = -1*np.ones((N_tot, N_AK_MAX), dtype=np.int32)  # TODO: Nested list
    which_connections = initialize_nested_lists(N_tot, dtype=np.int32) # XXX nested
    # which_connections_reference = -1*np.ones((N_tot, N_AK_MAX), dtype=np.int32)  # TODO: Nested list
    which_connections_reference = initialize_nested_lists(N_tot, dtype=np.int32) # XXX nested
    # get_size_gb(which_connections)
    # individual_rates = -1*np.ones((N_tot, N_AK_MAX), dtype=np.float32)  # TODO: Nested list
    individual_rates = initialize_nested_lists(N_tot, dtype=np.float32)
    # agents_in_state = -1*np.ones((N_states, N_tot), dtype=np.int32) # TODO: Nested list
    agents_in_state = initialize_nested_lists(N_states, dtype=np.int32)

    state_total_counts = np.zeros(N_states, dtype=np.int32)
    SIR_transition_rates = np.zeros(N_states, dtype=np.float32)

    N_connections = np.zeros(N_tot, dtype=np.int32)
    N_connections_reference = np.zeros(N_tot, dtype=np.int32)
    
    which_state = -1*np.ones(N_tot, dtype=np.int8)
    
    csMov = np.zeros(N_states, dtype=np.float64)
    csInf = np.zeros(N_states, dtype=np.float64)
    InfRat = np.zeros(N_tot, dtype=np.float64)

    infectious_state = 4 # This means the 5'th state
    SIR_transition_rates[:4] = lambda_E
    SIR_transition_rates[4:8] = lambda_I

    # age variables
    age_matrix_relative_interactions = calculate_age_proportions_1D(1.0, N_ages)
    age_relative_proportions = calculate_age_proportions_2D(age_mixing, N_ages)
    age_matrix = age_matrix_relative_interactions * age_relative_proportions * mu * N_tot
        
    # Hospitalization track variables
    H_N_states = 6 # number of states
    H_state_total_counts = np.zeros(H_N_states, dtype=np.int32)
    H_which_state = -1*np.ones(N_tot, dtype=np.int8)
    # H_agents_in_state = -1*np.ones((H_N_states, N_tot), dtype=np.int32) # XXX
    H_agents_in_state = initialize_nested_lists(H_N_states, dtype=np.int32)
    H_probability_matrix = np.ones((N_ages, H_N_states), dtype=np.float32) / H_N_states
    H_probability_matrix_csum = numba_cumsum(H_probability_matrix, axis=1)

    H_move_matrix = np.zeros((H_N_states, H_N_states, N_ages), dtype=np.float32)
    H_move_matrix[0, 1] = 0.3
    H_move_matrix[1, 2] = 1.0
    H_move_matrix[2, 1] = 0.6
    H_move_matrix[1, 4] = 0.1
    H_move_matrix[2, 3] = 0.1
    H_move_matrix[3, 4] = 1.0
    H_move_matrix[3, 5] = 0.1


    H_move_matrix_sum = np.sum(H_move_matrix, axis=1) 
    H_move_matrix_cumsum = numba_cumsum(H_move_matrix, axis=1) 

    H_cumsum_move = np.zeros(H_N_states, dtype=np.float64)


    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 
    # # # # # # # # # # # RATES AND CONNECTIONS # # # # # # # # # # # # # # # # # # # # # 
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 

    if verbose:
        print("Make rates and connections")

    connection_weight, infection_weight = initialize_connections_and_rates(N_tot, sigma_mu, beta, sigma_beta, beta_scaling)

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 
    # # # # # # # # # # # # # # # # AGES  # # # # # # # # # # # # # # # # # # # # # 
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 

    if verbose:
        print("Make ages")

    ages, ages_total_counts, ages_in_state, PT_ages, PC_ages, PP_ages = initialize_ages(N_tot, N_ages, connection_weight)

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 
    # # # # # # # # # # # CONNECT NODES # # # # # # # # # # # # # # # # # # # # # # # # #
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 
    
    if verbose:
        print("CONNECT NODES")

    connect_nodes(mu, epsilon_rho, rho, algo, PP_ages, N_connections, individual_rates, which_connections, which_connections_reference, coordinates, infection_weight, N_connections_reference, rho_scale, N_ages, age_matrix, ages_in_state, verbose)

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 
    # # # # # # # # # # # INITIAL INFECTIONS  # # # # # # # # # # # # # # # # # # # # # # # #
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 

    if verbose:
        print("INITIAL INFECTIONS")

    TotMov = make_initial_infections(N_init, which_state, state_total_counts, agents_in_state, csMov, N_connections_reference, which_connections, which_connections_reference, N_connections, individual_rates, SIR_transition_rates, ages_in_state, initial_ages_exposed)


    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 
    # # # # # # # # # # # RUN SIMULATION  # # # # # # # # # # # # # # # # # # # # # # # #
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # 

    if verbose:
        print("RUN SIMULATION")

    res = run_simulation(N_tot, TotMov, csMov, state_total_counts, agents_in_state, which_state, csInf, N_states, InfRat, SIR_transition_rates, infectious_state, N_connections, individual_rates, N_connections_reference, which_connections_reference, which_connections, ages, H_probability_matrix_csum, H_which_state, H_agents_in_state, H_state_total_counts, H_move_matrix_sum, H_cumsum_move, H_move_matrix_cumsum, mu, tau_I, N_contacts_remove, nts, verbose)

    return res



def single_run_and_save(filename, verbose=False):

    # filename = 'Data/ABN/N_tot__58000__N_init__100__N_ages__1__mu__40.0__sigma_mu__0.0__beta__0.01__sigma_beta__0.0__rho__0.0__lambda_E__1.0__lambda_I__1.0__epsilon_rho__0.01__beta_scaling__1.0__age_mixing__1.0__algo__2/N_tot__58000__N_init__100__N_ages__1__mu__40.0__sigma_mu__0.0__beta__0.01__sigma_beta__0.0__rho__0.0__lambda_E__1.0__lambda_I__1.0__epsilon_rho__0.01__beta_scaling__1.0__age_mixing__1.0__algo__2__ID__000.csv'

    cfg = filename_to_dict(filename)
    ID = filename_to_ID(filename)

    coordinates = np.load('Data/GPS_coordinates.npy')
    if cfg.N_tot > len(coordinates):
        raise AssertionError("N_tot cannot be larger than coordinates (number of generated houses in DK)")

    np.random.seed(ID)
    index = np.arange(len(coordinates))
    index_subset = np.random.choice(index, cfg.N_tot, replace=False)
    coordinates = coordinates[index_subset]

    res = single_run_numba(**cfg, ID=ID, coordinates=coordinates, verbose=verbose)
    out_time, out_state_counts, out_which_state, out_N_connections, out_which_connections, out_individual_rates, out_H_state_total_counts, out_ages = res
    
    header = [
             'Time', 
            'E1', 'E2', 'E3', 'E4', 
            'I1', 'I2', 'I3', 'I4', 
            'R',
            'H1', 'H2', 'ICU1', 'ICU2', 'R_H', 'D',
            ]

    df_time = pd.DataFrame(np.array(out_time), columns=header[0:1])
    df_states = pd.DataFrame(np.array(out_state_counts), columns=header[1:10])
    df_H_states = pd.DataFrame(np.array(out_H_state_total_counts), columns=header[10:])
    df = pd.concat([df_time, df_states, df_H_states], axis=1)#.convert_dtypes()
    assert sum(df_H_states.sum(axis=1) == df_states['R'])
    

    # make sure parent folder exists
    Path(filename).parent.mkdir(parents=True, exist_ok=True)
    # save csv file
    df.to_csv(filename, index=False)

    # save which_state, coordinates, and N_connections, once for each set of parameters
    if ID == 0:
        which_state = np.array(out_which_state)
        N_connections = np.array(out_N_connections)
        ages = np.array(out_ages)

        filename_animation = str(Path('Data_animation') / Path(filename).stem) + '.animation.hdf5'

        path = Path(filename_animation)
        path.parent.mkdir(parents=True, exist_ok=True)
        # joblib.dump([which_state, coordinates, N_connections], filename_animation.replace('hdf5', 'joblib')) 
        if path.exists():
            path.unlink()

        with h5py.File(filename_animation, "w") as f: # 
            f.create_dataset("which_state", data=which_state)
            f.create_dataset("coordinates", data=coordinates)
            f.create_dataset("N_connections", data=N_connections)
            f.create_dataset("ages", data=ages)
            f.create_dataset("cfg_str", data=str(cfg)) # import ast; ast.literal_eval(str(cfg))
            df_structured = np.array(df.to_records(index=False))
            f.create_dataset("df", data=df_structured) 
            
            g = awkward.hdf5(f)
            g["which_connections"] = awkward.fromiter(out_which_connections).astype(np.int32)
            g["individual_rates"] = awkward.fromiter(out_individual_rates)

            for key, val in cfg.items():
                f.attrs[key] = val

        # which_connections = awkward.fromiter(out_which_connections).astype(np.int32)
        # filename_which_connections = filename_animation.replace('animation.joblib', 'which_connections.parquet')
        # awkward.toparquet(filename_which_connections, which_connections)

        # individual_rates = awkward.fromiter(out_individual_rates)
        # filename_rates = filename_which_connections.replace('which_connections.parquet', 'rates.parquet')
        # awkward.toparquet(filename_rates, individual_rates)

    return None


# if False:

#     filename_animation = 'Data_animation/N_tot__58000__N_init__100__N_ages__1__mu__40.0__sigma_mu__0.0__beta__0.01__sigma_beta__0.0__rho__0.0__lambda_E__1.0__lambda_I__1.0__epsilon_rho__0.01__beta_scaling__1.0__age_mixing__1.0__algo__2__ID__000.animation.hdf5'

#     f = h5py.File(filename_animation, "r")
#     # with h5py.File(filename_animation, "w") as f:
#     f["which_state"].value
#     f["coordinates"]
#     f["N_connections"]
#     ages = f["ages"].value

#     df = pd.DataFrame(f["df"].value)

#     f["which_connections"] 
#     g = awkward.hdf5(f)
#     g["which_connections"] 
#     g["individual_rates"] 

