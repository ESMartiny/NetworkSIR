from matplotlib.pyplot import xticks
import numpy as np
import pandas as pd

from pathlib import Path
import multiprocessing as mp
import matplotlib.pyplot as plt
import time as Time
import h5py

# import psutil
from resource import getrusage, RUSAGE_SELF
import warnings
from importlib import reload
from contexttimer import Timer
import os
from IPython.display import display
from contexttimer import Timer


import numba as nb
from numba import (
    njit,
    prange,
    objmode,
    typeof,
)  # conda install -c numba/label/dev numba
from numba.typed import List, Dict

# from numba.types import Set
from numba.core.errors import (
    NumbaTypeSafetyWarning,
    NumbaExperimentalFeatureWarning,
    NumbaPendingDeprecationWarning,
)


import awkward as awkward0  # conda install awkward0, conda install -c conda-forge pyarrow
import awkward1 as ak  # pip install awkward1

try:
    from src import utils
    from src import simulation_utils
except ImportError:
    import utils
    import simulation_utils

np.set_printoptions(linewidth=200)
do_memory_tracking = False


@njit
def set_connections_weight(my_connection_weight, agent, sigma_mu):
    """ How introvert / extrovert you are. How likely you are at having many contacts in your network."""
    if np.random.rand() < sigma_mu:
        my_connection_weight[agent] = 0.1 - np.log(np.random.rand())
    else:
        my_connection_weight[agent] = 1.1


@njit
def set_infection_weight(my_infection_weight, agent, sigma_beta, beta):
    " How much of a super sheader are you?"
    if np.random.rand() < sigma_beta:
        my_infection_weight[agent] = -np.log(np.random.rand()) * beta
    else:
        my_infection_weight[agent] = beta


#%%


@njit
def place_and_connect_families(
    N_tot, people_in_household, age_distribution_per_people_in_household, coordinates, sigma_mu, sigma_beta, beta,
):

    all_indices = np.arange(N_tot)
    np.random.shuffle(all_indices)

    my_age = np.zeros(N_tot, dtype=np.uint8)
    my_connections = utils.initialize_nested_lists(N_tot, dtype=np.uint32)
    my_connections_type = utils.initialize_nested_lists(N_tot, dtype=np.uint8)
    my_coordinates = np.zeros((N_tot, 2), dtype=np.float32)

    my_connection_weight = np.ones(N_tot, dtype=np.float32)
    my_infection_weight = np.ones(N_tot, dtype=np.float64)

    my_number_of_contacts = np.zeros(N_tot, dtype=np.uint16)

    N_dim_people_in_household, N_ages = age_distribution_per_people_in_household.shape
    assert N_dim_people_in_household == len(people_in_household)
    people_index_to_value = np.arange(1, N_dim_people_in_household + 1)

    counter_ages = np.zeros(N_ages, dtype=np.uint16)
    agents_in_age_group = utils.initialize_nested_lists(N_ages, dtype=np.uint32)

    mu_counter = 0
    agent = 0
    do_continue = True
    while do_continue:

        agent0 = agent

        house_index = all_indices[agent]

        N_people_in_house_index = simulation_utils.rand_choice_nb(people_in_household)
        N_people_in_house = people_index_to_value[N_people_in_house_index]

        # if N_in_house would increase agent to over N_tot,
        # set N_people_in_house such that it fits and break loop
        if agent + N_people_in_house >= N_tot:
            N_people_in_house = N_tot - agent
            do_continue = False

        for _ in range(N_people_in_house):

            age_index = simulation_utils.rand_choice_nb(
                age_distribution_per_people_in_household[N_people_in_house_index]
            )

            age = age_index  # just use age index as substitute for age
            my_age[agent] = age
            counter_ages[age_index] += 1
            agents_in_age_group[age_index].append(np.uint32(agent))

            my_coordinates[agent] = coordinates[house_index]

            set_connections_weight(my_connection_weight, agent, sigma_mu)
            set_infection_weight(my_infection_weight, agent, sigma_beta, beta)

            agent += 1

        # add agents to each others networks (connections)
        for agent1 in range(agent0, agent0 + N_people_in_house):
            for agent2 in range(agent1, agent0 + N_people_in_house):
                if agent1 != agent2:
                    my_connections[agent1].append(np.uint32(agent2))
                    my_connections[agent2].append(np.uint32(agent1))
                    my_connections_type[agent1].append(np.uint8(0))
                    my_connections_type[agent2].append(np.uint8(0))
                    # my_index_in_contact[agent2].append(my_number_of_contacts[agent1])
                    my_number_of_contacts[agent1] += 1
                    my_number_of_contacts[agent2] += 1
                    mu_counter += 1

    agents_in_age_group = utils.nested_lists_to_list_of_array(agents_in_age_group)

    return (
        my_age,
        my_connections,
        my_coordinates,
        my_connection_weight,
        my_infection_weight,
        mu_counter,
        counter_ages,
        agents_in_age_group,
        my_connections_type,
        my_number_of_contacts,
    )


#%%


@njit
def update_node_connections(
    my_connections,
    coordinates,
    rho_tmp,
    rho_scale,
    agent1,
    agent2,
    my_connections_type,
    my_number_of_contacts,
    connection_type,
):
    connect_and_stop = False
    if agent1 != agent2:

        if rho_tmp == 0:
            connect_and_stop = True
        else:
            r = utils.haversine_scipy(coordinates[agent1], coordinates[agent2])
            if np.exp(-r * rho_tmp / rho_scale) > np.random.rand():
                connect_and_stop = True

        if connect_and_stop:

            if agent1 not in my_connections[agent2] and agent2 not in my_connections[agent1]:

                my_connections[agent1].append(np.uint32(agent2))
                my_connections[agent2].append(np.uint32(agent1))

                my_connections_type[agent1].append(np.uint8(connection_type))
                my_connections_type[agent2].append(np.uint8(connection_type))

                # my_index_in_contact[agent2].append(my_number_of_contacts[agent1])
                # my_index_in_contact[agent1].append(my_number_of_contacts[agent2])

                my_number_of_contacts[agent1] += 1
                my_number_of_contacts[agent2] += 1

            # else:
            #     print(agent1, my_connections[agent2])
            #     print(agent2, my_connections[agent1])

    return connect_and_stop


@njit
def run_algo_other(
    agents_in_age_group,
    age1,
    age2,
    my_connections,
    my_connections_type,
    my_number_of_contacts,
    coordinates,
    rho_tmp,
    rho_scale,
):
    while True:
        # agent1 = np.searchsorted(PP_ages[m_i], np.random.rand())
        # agent1 = agents_in_age_group[m_i][agent1]
        # TODO: Add connection weights
        agent1 = np.random.choice(agents_in_age_group[age1])
        agent2 = np.random.choice(agents_in_age_group[age2])
        do_stop = update_node_connections(
            my_connections,
            coordinates,
            rho_tmp,
            rho_scale,
            agent1,
            agent2,
            my_connections_type,
            my_number_of_contacts,
            connection_type=2,
        )
        if do_stop:
            break


@njit
def run_algo_work(
    agents_in_age_group,
    age1,
    age2,
    my_connections,
    my_connections_type,
    my_number_of_contacts,
    coordinates,
    rho_tmp,
    rho_scale,
):
    # ra1 = np.random.rand()
    # agent1 = np.searchsorted(PP_ages[m_i], ra1)
    # agent1 = agents_in_age_group[m_i][agent1]
    # TODO: Add connection weights
    agent1 = np.random.choice(agents_in_age_group[age1])

    while True:
        agent2 = np.random.choice(agents_in_age_group[age2])
        rho_tmp *= 0.9995
        do_stop = update_node_connections(
            my_connections,
            coordinates,
            rho_tmp,
            rho_scale,
            agent1,
            agent2,
            my_connections_type,
            my_number_of_contacts,
            connection_type=1,
        )

        if do_stop:
            break


@njit
def nb_find_two_age_groups(N_ages, matrix):
    a = 0
    ra = np.random.rand()
    for i in range(N_ages):
        for j in range(N_ages):
            a += matrix[i, j]
            if a > ra:
                age1, age2 = i, j
                return age1, age2
    raise AssertionError("nb_find_two_age_groups couldn't find two age groups")


@njit
def nb_connect_work_and_others(
    N_tot,
    N_ages,
    mu_counter,
    mu,
    work_other_ratio,
    matrix_work,
    matrix_other,
    run_algo_work,
    run_algo_other,
    rho,
    rho_scale,
    epsilon_rho,
    coordinates,
    agents_in_age_group,
    my_connections,
    my_connections_type,
    my_number_of_contacts,
):

    while mu_counter < mu / 2 * N_tot:

        ra_work_other = np.random.rand()
        if ra_work_other < work_other_ratio:
            matrix = matrix_work
            run_algo = run_algo_work
            work = True
        else:
            matrix = matrix_other
            run_algo = run_algo_other
            work = False

        age1, age2 = nb_find_two_age_groups(N_ages, matrix)

        if np.random.rand() > epsilon_rho:
            rho_tmp = rho
        else:
            rho_tmp = 0.0

        run_algo(
            agents_in_age_group,
            age1,
            age2,
            my_connections,
            my_connections_type,
            my_number_of_contacts,
            coordinates,
            rho_tmp,
            rho_scale,
        )
        mu_counter += 1


@njit
def initialize_tents(coordinates, N_tot, N_tents):

    tent_positions = np.zeros((N_tents, 2), np.float32)
    for i in range(N_tents):
        tent_positions[i] = coordinates[np.random.randint(N_tot)]

    my_closest_tent = np.zeros(N_tot, np.int16)
    for agent in range(N_tot):
        closest_tent = -1
        r_min = 10e10
        for i_tent, tent_position in enumerate(tent_positions):
            r = utils.haversine_scipy(coordinates[agent], tent_position)
            if r < r_min:
                r_min = r
                closest_tent = i_tent
        my_closest_tent[agent] = closest_tent

    return my_closest_tent, tent_positions


@njit
def nb_make_initial_infections(
    N_init,
    my_state,
    state_total_counts,
    agents_in_state,
    g_cumulative_sum_of_state_changes,
    my_connections,
    my_number_of_contacts,
    my_rates,
    SIR_transition_rates,
    agents_in_age_group,
    initial_ages_exposed,
    N_infectious_states,
    coordinates,
    make_random_infections,
):

    if do_memory_tracking:
        with objmode():
            track_memory("Initial Infections")

    g_total_sum_of_state_changes = 0.0
    N_tot = len(my_number_of_contacts)
    non_infectable_agents = np.zeros(N_tot, dtype=np.bool_)

    possible_agents = List()
    for age_exposed in initial_ages_exposed:
        for agent in agents_in_age_group[age_exposed]:
            possible_agents.append(np.uint32(agent))

    ##  Standard outbreak type, infecting randomly
    if make_random_infections:
        initial_agents_to_infect = np.random.choice(np.asarray(possible_agents), size=N_init, replace=False)

    # Local outbreak type, infecting around a point:
    else:

        rho_init = 100
        rho_init_scale = 1000

        outbreak_agent = np.random.randint(N_tot)  # this is where the outbreak starts

        initial_agents_to_infect = List()
        initial_agents_to_infect.append(np.uint32(outbreak_agent))

        while len(initial_agents_to_infect) < N_init:
            proposed_agent = np.random.randint(N_tot)

            r = utils.haversine_scipy(coordinates[outbreak_agent], coordinates[proposed_agent])
            if np.exp(-r * rho_init / rho_init_scale) > np.random.rand():
                initial_agents_to_infect.append(np.uint32(proposed_agent))
        initial_agents_to_infect = np.asarray(initial_agents_to_infect, dtype=np.uint32)

    ##  Now make initial infections
    for i, agent in enumerate(initial_agents_to_infect):
        new_state = np.random.randint(N_infectious_states)  # E1, E2, E3 or E4
        my_state[agent] = new_state

        agents_in_state[new_state].append(np.uint32(agent))
        state_total_counts[new_state] += 1

        # g_total_sum_of_state_changes += SIR_transition_rates[new_state]
        # g_cumulative_sum_of_state_changes[new_state:] += SIR_transition_rates[new_state]
        g_total_sum_of_state_changes += SIR_transition_rates[new_state]  # 'g_' = gillespie variable
        g_cumulative_sum_of_state_changes[new_state:] += SIR_transition_rates[new_state]

    if do_memory_tracking:
        with objmode():
            track_memory()

    return g_total_sum_of_state_changes  # , non_infectable_agents


#%%


@njit
def nb_run_simulation(
    N_tot,
    g_total_sum_of_state_changes,
    g_cumulative_sum_of_state_changes,
    state_total_counts,
    agents_in_state,
    my_state,
    g_cumulative_sum_infection_rates,
    N_states,
    my_sum_of_rates,
    SIR_transition_rates,
    N_infectious_states,
    my_number_of_contacts,
    my_rates,
    my_connections,
    my_age,
    individual_infection_counter,
    H_probability_matrix_csum,
    H_my_state,
    H_agents_in_state,
    H_state_total_counts,
    H_move_matrix_sum,
    H_cumsum_move,
    H_move_matrix_cumsum,
    nts,
    verbose,
    my_connections_type,
):

    # if do_memory_tracking:
    #     with objmode():
    #         track_memory('Simulation')

    out_time = List()
    out_state_counts = List()
    out_my_state = List()
    # out_infection_counter = List()
    # out_my_number_of_contacts = List()
    N_positive_tested = List()

    daily_counter = 0

    # g_total_sum = 0.0
    # g_total_sum_infections = 0.0
    # click = 0
    # step_number = 0
    # g_cumulative_sum = 0.0
    # real_time = 0.0

    g_total_sum = 0.0
    g_total_sum_infections = 0.0
    g_cumulative_sum = 0.0

    click = 0
    step_number = 0

    real_time = 0.0

    H_tot_move = 0

    time_inf = np.zeros(N_tot, np.float32)
    bug_contacts = np.zeros(N_tot, np.int32)

    # agent_can_infect = np.zeros(N_tot, dtype=np.bool_)

    s_counter = np.zeros(4)

    infectious_states = {4, 5, 6, 7}  # TODO: fix

    # if do_memory_tracking:
    #     with objmode():
    #         track_memory()

    # Run the simulation ################################
    continue_run = True
    while continue_run:

        s = 0

        step_number += 1
        g_total_sum = g_total_sum_of_state_changes + g_total_sum_infections  # + H_tot_move XXX Hospitals

        dt = -np.log(np.random.rand()) / g_total_sum
        real_time += dt

        g_cumulative_sum = 0.0
        ra1 = np.random.rand()

        #######/ Here we move between infected between states
        accept = False
        if g_total_sum_of_state_changes / g_total_sum > ra1:

            s = 1

            x = g_cumulative_sum_of_state_changes / g_total_sum
            state_now = np.searchsorted(x, ra1)
            state_after = state_now + 1

            agent = utils.numba_random_choice_list(agents_in_state[state_now])

            # We have chosen agent to move -> here we move it
            agents_in_state[state_after].append(agent)
            agents_in_state[state_now].remove(agent)

            my_state[agent] += 1

            state_total_counts[state_now] -= 1
            state_total_counts[state_after] += 1

            g_total_sum_of_state_changes -= SIR_transition_rates[state_now]
            g_total_sum_of_state_changes += SIR_transition_rates[state_after]

            g_cumulative_sum_of_state_changes[state_now] -= SIR_transition_rates[state_now]
            g_cumulative_sum_of_state_changes[state_after:] += (
                SIR_transition_rates[state_after] - SIR_transition_rates[state_now]
            )

            g_cumulative_sum_infection_rates[state_now] -= my_sum_of_rates[agent]

            accept = True

            # Moves TO infectious State from non-infectious
            if my_state[agent] == N_infectious_states:
                for contact, rate in zip(my_connections[agent], my_rates[agent]):  # Loop over row agent
                    # if contact is susceptible
                    if my_state[contact] == -1:
                        g_total_sum_infections += rate
                        my_sum_of_rates[agent] += rate
                        g_cumulative_sum_infection_rates[my_state[agent] :] += rate

            # If this moves to Recovered state
            if my_state[agent] == N_states - 1:
                for contact, rate in zip(my_connections[agent], my_rates[agent]):
                    # if contact is susceptible
                    if my_state[contact] == -1:
                        g_total_sum_infections -= rate
                        my_sum_of_rates[agent] -= rate
                        g_cumulative_sum_infection_rates[my_state[agent] :] -= rate

        #######/ Here we infect new states
        else:
            s = 2

            x = (g_total_sum_of_state_changes + g_cumulative_sum_infection_rates) / g_total_sum
            state_now = np.searchsorted(x, ra1)
            g_cumulative_sum = (
                g_total_sum_of_state_changes + g_cumulative_sum_infection_rates[state_now - 1]
            ) / g_total_sum  # important change from [state_now] to [state_now-1]

            agent_getting_infected = -1
            for agent in agents_in_state[state_now]:

                # suggested cumulative sum
                suggested_cumulative_sum = g_cumulative_sum + my_sum_of_rates[agent] / g_total_sum

                if suggested_cumulative_sum > ra1:
                    for rate, contact in zip(my_rates[agent], my_connections[agent]):

                        # if contact is susceptible
                        if my_state[contact] == -1:

                            g_cumulative_sum += rate / g_total_sum

                            # here agent infect contact
                            if g_cumulative_sum > ra1:
                                my_state[contact] = 0
                                agents_in_state[0].append(contact)
                                state_total_counts[0] += 1
                                g_total_sum_of_state_changes += SIR_transition_rates[0]
                                g_cumulative_sum_of_state_changes += SIR_transition_rates[0]
                                accept = True
                                # non_infectable_agents[contact] = True
                                agent_getting_infected = contact
                                break
                else:
                    g_cumulative_sum = suggested_cumulative_sum

                if accept:
                    break

            if agent_getting_infected == -1:
                print("Error here", accept, agent_getting_infected, step_number)
                break

            # Here we update infection lists so that newly infected cannot be infected again

            # loop over contacts of the newly infected agent in order to:
            # 1) remove newly infected agent from contact list (find_myself) by setting rate to 0
            # 2) remove rates from contacts gillespie sums (only if they are in infections state (I))
            for contact_of_agent_getting_infected in my_connections[agent_getting_infected]:

                # loop over indexes of the contact to find_myself and set rate to 0
                for ith_contact_of_agent_getting_infected in range(
                    my_number_of_contacts[contact_of_agent_getting_infected]
                ):

                    find_myself = my_connections[contact_of_agent_getting_infected][
                        ith_contact_of_agent_getting_infected
                    ]

                    # check if the contact found is myself
                    if find_myself == agent_getting_infected:

                        rate = my_rates[contact_of_agent_getting_infected][ith_contact_of_agent_getting_infected]

                        # set rates to myself to 0 (I cannot get infected again)
                        my_rates[contact_of_agent_getting_infected][ith_contact_of_agent_getting_infected] = 0

                        # if the contact can infect, then remove the rates from the overall gillespie accounting
                        if my_state[contact_of_agent_getting_infected] in infectious_states:
                            g_total_sum_infections -= rate
                            my_sum_of_rates[contact_of_agent_getting_infected] -= rate
                            g_cumulative_sum_infection_rates[my_state[contact_of_agent_getting_infected] :] -= rate

                        break

                # else:
                #     continue

        ################

        # if np.abs(g_cumulative_sum_infection_rates[7] - g_cumulative_sum_infection_rates[8]) > 0.001:
        #     print("g_cumulative_sum_infection_rates 78")
        #     print(step_number, s, agent, g_cumulative_sum_infection_rates)
        #     break

        # if np.any(g_cumulative_sum_infection_rates < -0.001):
        #     print("g_cumulative_sum_infection_rates negative")
        #     print(step_number, s, agent, g_cumulative_sum_infection_rates)
        #     break

        # if np.abs(g_cumulative_sum_infection_rates[8] - g_total_sum_infections) > 0.001:
        #     print("g_cumulative_sum_infection_rates g_total_sum_infections")
        #     print(step_number, s, agent, g_cumulative_sum_infection_rates, g_total_sum_infections)
        #     break

        # for agent in range(N_tot):
        #     if np.abs(np.sum(my_rates[agent]) - my_sum_of_rates[agent]) > 0.001:
        #         print(step_number, s, agent, my_rates[agent], np.sum(my_rates[agent]), my_sum_of_rates[agent])

        # if np.abs(g_cumulative_sum_infection_rates[7] - g_total_sum_infections) > 0.001:
        #     print(step_number, s, g_cumulative_sum_infection_rates, g_total_sum_infections)

        if nts * click < real_time:

            daily_counter += 1
            out_time.append(real_time)
            out_state_counts.append(state_total_counts.copy())

            if daily_counter >= 10:

                daily_counter = 0
                out_my_state.append(my_state.copy())

                # tent testing
                # N_daily_tests = 10_000
                N_daily_tests = 0
                f_test_succes = 0.8

                g_total_sum_infections, n_positive_tested = nb_daily_tent_test(
                    N_daily_tests,
                    f_test_succes,
                    N_tot,
                    my_state,
                    N_infectious_states,
                    N_states,
                    my_number_of_contacts,
                    my_connections,
                    my_rates,
                    my_connections_type,
                    infectious_states,
                    g_total_sum_infections,
                    my_sum_of_rates,
                    g_cumulative_sum_infection_rates,
                )
                N_positive_tested.append(n_positive_tested)

                # if do_memory_tracking:
                #     with objmode():
                #         track_memory()

            click += 1

        # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
        # # # # # # # # # # # BUG CHECK  # # # # # # # # # # # # # # # # # # # # # # # #
        # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

        (continue_run, g_total_sum_of_state_changes, g_total_sum_infections,) = nb_do_bug_check(
            step_number,
            continue_run,
            g_total_sum_infections,
            g_total_sum_of_state_changes,
            verbose,
            state_total_counts,
            N_states,
            N_tot,
            accept,
            g_cumulative_sum_of_state_changes,
            ra1,
            s,
            x,
            g_cumulative_sum_infection_rates,
        )

        s_counter[s] += 1

    if verbose:
        print("Simulation step_number, ", step_number)
        print("s_counter", s_counter)
        print("N_daily_tests", N_daily_tests)
        print("N_positive_tested", N_positive_tested)

    return out_time, out_state_counts, out_my_state  # , out_H_state_total_counts


@njit
def nb_daily_tent_test(
    N_daily_tests,
    f_test_succes,
    N_tot,
    my_state,
    N_infectious_states,
    N_states,
    my_number_of_contacts,
    my_connections,
    my_rates,
    my_connections_type,
    infectious_states,
    g_total_sum_infections,
    my_sum_of_rates,
    g_cumulative_sum_infection_rates,
):

    n_positive_tested = 0

    for _ in range(N_daily_tests):
        agent = np.random.randint(N_tot)

        # only if in I state and  un-noticed
        if (my_state[agent] in infectious_states) and (np.random.rand() < f_test_succes):

            # agent_tested_positive.append(agent)
            n_positive_tested += 1

            for i in range(my_number_of_contacts[agent]):
                contact = my_connections[agent][i]
                rate = my_rates[agent][i]
                connection_type = my_connections_type[agent][i]

                # only close work/other contacts
                if my_state[contact] == -1 and connection_type > -1:
                    g_total_sum_infections -= rate
                    my_sum_of_rates[agent] -= rate
                    g_cumulative_sum_infection_rates[my_state[agent] :] -= rate
                    my_rates[agent][i] = 0

    return g_total_sum_infections, n_positive_tested


#%%


@njit
def nb_do_bug_check(
    step_number,
    continue_run,
    g_total_sum_infections,
    g_total_sum_of_state_changes,
    verbose,
    state_total_counts,
    N_states,
    N_tot,
    accept,
    g_cumulative_sum_of_state_changes,
    ra1,
    s,
    x,
    g_cumulative_sum_infection_rates,
):

    if step_number > 100_000_000:
        print("step_number > 100_000_000")
        continue_run = False

    if (g_total_sum_infections + g_total_sum_of_state_changes < 0.0001) and (
        g_total_sum_of_state_changes + g_total_sum_infections > -0.00001
    ):
        continue_run = False
        if verbose:
            print("Equilibrium")

    if state_total_counts[N_states - 1] > N_tot - 10:
        if verbose:
            print("2/3 through")
        continue_run = False

    # Check for bugs
    if not accept:
        print("\nNo Chosen rate")
        print("s: \t", s)
        print("g_total_sum_infections: \t", g_total_sum_infections)
        print("g_cumulative_sum_infection_rates: \t", g_cumulative_sum_infection_rates)
        print("g_cumulative_sum_of_state_changes: \t", g_cumulative_sum_of_state_changes)
        print("x: \t", x)
        print("ra1: \t", ra1)
        continue_run = False

    if (g_total_sum_of_state_changes < 0) and (g_total_sum_of_state_changes > -0.001):
        g_total_sum_of_state_changes = 0

    if (g_total_sum_infections < 0) and (g_total_sum_infections > -0.001):
        g_total_sum_infections = 0

    if (g_total_sum_of_state_changes < 0) or (g_total_sum_infections < 0):
        print("\nNegative Problem", g_total_sum_of_state_changes, g_total_sum_infections)
        print("s: \t", s)
        print("g_total_sum_infections: \t", g_total_sum_infections)
        print("g_cumulative_sum_infection_rates: \t", g_cumulative_sum_infection_rates)
        print("g_cumulative_sum_of_state_changes: \t", g_cumulative_sum_of_state_changes)
        print("x: \t", x)
        print("ra1: \t", ra1)
        continue_run = False

    return continue_run, g_total_sum_of_state_changes, g_total_sum_infections


#%%


class Simulation:
    def __init__(self, filename, verbose=False, do_track_memory=True):

        self.verbose = verbose

        self._Filename = Filename = simulation_utils.Filename(filename)

        self.cfg = Filename.simulation_parameters
        self.ID = Filename.ID

        self.filenames = {}
        self.filename = self.filenames["filename"] = Filename.filename
        self.filenames["network_initialisation"] = Filename.filename_network_initialisation
        self.filenames["network_network"] = Filename.filename_network

        utils.set_numba_random_seed(self.ID)

        self._prepare_memory_tracking(do_track_memory)

    def _prepare_memory_tracking(self, do_track_memory=True):
        self.filenames["memory"] = memory_file = self._Filename.memory_filename
        self.do_track_memory = do_track_memory  # if self.ID == 0 else False
        self.time_start = Time.time()

        search_string = "Saving network initialization"

        if utils.file_exists(memory_file) and simulation_utils.does_file_contains_string(memory_file, search_string):
            self.time_start -= simulation_utils.get_search_string_time(memory_file, search_string)
            self.track_memory("Appending to previous network initialization")
        else:
            utils.make_sure_folder_exist(self.filenames["memory"], delete_file_if_exists=True)

        global track_memory
        track_memory = self.track_memory

    @property
    def current_memory_usage(self):
        "Returns current memory usage of entire process in GiB"
        # process = psutil.Process()
        # return process.memory_info().rss / 2**30
        return getrusage(RUSAGE_SELF).ru_maxrss / 2 ** 30

    def track_memory(self, s=None):
        if self.do_track_memory:
            with open(self.filenames["memory"], "a") as file:
                if s:
                    print("#" + s, file=file)
                time = Time.time() - self.time_start
                print(time, self.current_memory_usage, file=file, sep="\t")  # GiB

    def _initialize_network(self):

        cfg = self.cfg
        self.track_memory("Loading Coordinates")
        self.coordinates, self.coordinate_indices = simulation_utils.load_coordinates(
            self._Filename.coordinates_filename, cfg.N_tot, self.ID
        )

        if self.verbose:
            print("INITIALIZE NETWORK")
        self.track_memory("Initialize Network")

        rho_scale = 1000  # scale factor of rho

        (people_in_household, age_distribution_per_people_in_household,) = simulation_utils.load_household_data(
            self._Filename.household_data_filenames
        )
        (N_dim_people_in_household, N_ages,) = age_distribution_per_people_in_household.shape

        if self.verbose:
            print("Families")
        (
            my_age,
            my_connections,
            my_coordinates,
            my_connection_weight,
            my_infection_weight,
            mu_counter,
            counter_ages,
            agents_in_age_group,
            my_connections_type,
            my_number_of_contacts,
        ) = place_and_connect_families(
            cfg.N_tot,
            people_in_household,
            age_distribution_per_people_in_household,
            self.coordinates,
            cfg.sigma_mu,
            cfg.sigma_beta,
            cfg.beta,
        )

        if self.verbose:
            print("Using uniform work and other matrices")
        # matrix_work = np.random.random((N_ages, N_ages))
        matrix_work = np.ones((N_ages, N_ages))
        matrix_work = matrix_work * counter_ages * counter_ages.reshape((-1, 1))
        matrix_work = matrix_work / matrix_work.sum()

        # matrix_other = np.random.random((N_ages, N_ages))
        matrix_other = np.ones((N_ages, N_ages))
        matrix_other = matrix_other * counter_ages * counter_ages.reshape((-1, 1))
        matrix_other = matrix_other / matrix_other.sum()

        work_other_ratio = 0.5  # 20% work, 80% other

        if self.verbose:
            print("Connecting work and others, currently slow, please wait")
        nb_connect_work_and_others(
            cfg.N_tot,
            N_ages,
            mu_counter,
            cfg.mu,
            work_other_ratio,
            matrix_work,
            matrix_other,
            run_algo_work,
            run_algo_other,
            cfg.rho,
            rho_scale,
            cfg.epsilon_rho,
            self.coordinates,
            agents_in_age_group,
            my_connections,
            my_connections_type,
            my_number_of_contacts,
        )

        self.counter_ages = counter_ages
        self.agents_in_age_group = agents_in_age_group
        self.my_number_of_contacts = my_number_of_contacts
        self.my_infection_weight = my_infection_weight

        # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
        # # # # # # # # # # # # Find closests test tents  # # # # # # # # # # # # # # # # # # #
        # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

        if self.verbose:
            print("CONNECT TENTS")
        self.track_memory("Connect tents")

        my_closest_tent, tent_positions = initialize_tents(self.coordinates, self.cfg.N_tot, N_tents=100)

        return (
            my_connections,
            my_connections_type,
            my_number_of_contacts,
            my_infection_weight,
            my_age,
            agents_in_age_group,
            my_closest_tent,
            tent_positions,
        )

    def _save_network_initalization(
        self,
        my_age,
        agents_in_age_group,
        my_number_of_contacts,
        my_infection_weight,
        my_closest_tent,
        tent_positions,
        my_connections,
        my_connections_type,
        time_elapsed,
    ):
        self.track_memory("Saving network initialization")
        utils.make_sure_folder_exist(self.filenames["network_initialisation"])
        with h5py.File(self.filenames["network_initialisation"], "w") as f:  #
            f.create_dataset("cfg_str", data=str(self.cfg))  # import ast; ast.literal_eval(str(cfg))
            f.create_dataset("my_age", data=my_age)
            f.create_dataset("my_number_of_contacts", data=my_number_of_contacts)
            f.create_dataset("my_infection_weight", data=my_infection_weight)
            f.create_dataset("my_closest_tent", data=my_closest_tent)
            f.create_dataset("tent_positions", data=tent_positions)
            awkward0.hdf5(f)["my_connections"] = ak.to_awkward0(my_connections)
            awkward0.hdf5(f)["my_connections_type"] = ak.to_awkward0(my_connections_type)
            awkward0.hdf5(f)["agents_in_age_group"] = ak.to_awkward0(agents_in_age_group)
            for key, val in self.cfg.items():
                f.attrs[key] = val
            f.create_dataset("time_elapsed", data=time_elapsed)

    def _load_network_initalization(self):
        self.track_memory("Loading network initialization")
        with h5py.File(self.filenames["network_initialisation"], "r") as f:
            my_age = f["my_age"][()]
            my_number_of_contacts = f["my_number_of_contacts"][()]
            my_infection_weight = f["my_infection_weight"][()]
            my_closest_tent = f["my_closest_tent"][()]
            tent_positions = f["tent_positions"][()]
            my_connections = awkward0.hdf5(f)["my_connections"]
            my_connections_type = awkward0.hdf5(f)["my_connections_type"]
            agents_in_age_group = awkward0.hdf5(f)["agents_in_age_group"]
        self.track_memory("Loading Coordinates")
        self.coordinates, self.coordinate_indices = simulation_utils.load_coordinates(
            self._Filename.coordinates_filename, self.cfg.N_tot, self.ID
        )
        return (
            my_age,
            ak.from_awkward0(agents_in_age_group),
            my_number_of_contacts,
            my_infection_weight,
            my_closest_tent,
            tent_positions,
            ak.from_awkward0(my_connections),
            ak.from_awkward0(my_connections_type),
        )

    def initialize_network(self, force_rerun=False, save_initial_network=True):
        utils.set_numba_random_seed(self.ID)

        OSError_flag = False

        # try to load file (except if forced to rerun)
        if not force_rerun:
            try:
                (
                    my_age,
                    agents_in_age_group,
                    my_number_of_contacts,
                    my_infection_weight,
                    my_closest_tent,
                    tent_positions,
                    my_connections,
                    my_connections_type,
                ) = self._load_network_initalization()
                if self.verbose:
                    print(f"{self.filenames['network_initialisation']} exists")
            except OSError:
                if self.verbose:
                    print(f"{self.filenames['network_initialisation']} had OSError, creating it")
                OSError_flag = True

        # if ran into OSError above or forced to rerun:
        if OSError_flag or force_rerun:

            if self.verbose and not OSError_flag:
                print(f"{self.filenames['network_initialisation']} does not exist, creating it")

            with Timer() as t:
                (
                    my_connections,
                    my_connections_type,
                    my_number_of_contacts,
                    my_infection_weight,
                    my_age,
                    agents_in_age_group,
                    my_closest_tent,
                    tent_positions,
                ) = self._initialize_network()
            my_connections = utils.nested_list_to_awkward_array(my_connections)
            my_connections_type = utils.nested_list_to_awkward_array(my_connections_type)

            if save_initial_network:
                try:
                    self._save_network_initalization(
                        my_age=my_age,
                        agents_in_age_group=agents_in_age_group,
                        my_number_of_contacts=ak.to_awkward0(my_number_of_contacts),
                        my_infection_weight=my_infection_weight,
                        my_closest_tent=my_closest_tent,
                        tent_positions=tent_positions,
                        my_connections=ak.to_awkward0(my_connections),
                        my_connections_type=ak.to_awkward0(my_connections_type),
                        time_elapsed=t.elapsed,
                    )
                except OSError as e:
                    print(f"\nSkipped saving network initialization for {self.filenames['network_initialisation']}")
                    # print(e)

        self.my_age = my_age
        self.agents_in_age_group = agents_in_age_group
        self.N_ages = len(self.agents_in_age_group)
        self.my_connections = utils.MutableArray(my_connections)
        self.my_connections_type = utils.MutableArray(my_connections_type)
        self.my_number_of_contacts = my_number_of_contacts
        self.my_infection_weight = my_infection_weight
        self.my_closest_tent = my_closest_tent
        self.tent_positions = tent_positions

    def make_initial_infections(self):
        utils.set_numba_random_seed(self.ID)

        if self.verbose:
            print("INITIAL INFECTIONS")
        self.track_memory("Numba Compilation")

        cfg = self.cfg

        np.random.seed(self.ID)

        self.nts = 0.1  # Time step (0.1 - ten times a day)
        self.N_states = 9  # number of states
        self.N_infectious_states = 4  # This means the 5'th state
        self.initial_ages_exposed = np.arange(self.N_ages)  # means that all ages are exposed
        make_random_infections = True

        self.my_rates = simulation_utils.initialize_my_rates(self.my_infection_weight, self.my_number_of_contacts)

        self.my_state = np.full(cfg.N_tot, -1, dtype=np.int8)
        self.state_total_counts = np.zeros(self.N_states, dtype=np.uint32)
        self.agents_in_state = utils.initialize_nested_lists(self.N_states, dtype=np.uint32)

        self.g_cumulative_sum_of_state_changes = np.zeros(self.N_states, dtype=np.float64)
        self.g_cumulative_sum_infection_rates = np.zeros(self.N_states, dtype=np.float64)
        self.my_sum_of_rates = np.zeros(cfg.N_tot, dtype=np.float64)

        self.SIR_transition_rates = simulation_utils.initialize_SIR_transition_rates(
            self.N_states, self.N_infectious_states, cfg
        )

        self.g_total_sum_of_state_changes = nb_make_initial_infections(
            cfg.N_init,
            self.my_state,
            self.state_total_counts,
            self.agents_in_state,
            self.g_cumulative_sum_of_state_changes,
            self.my_connections.array,
            self.my_number_of_contacts,
            self.my_rates.array,
            self.SIR_transition_rates,
            self.agents_in_age_group,
            self.initial_ages_exposed,
            self.N_infectious_states,
            self.coordinates,
            make_random_infections,
        )

    def run_simulation(self):
        utils.set_numba_random_seed(self.ID)

        if self.verbose:
            print("RUN SIMULATION")
        self.track_memory("Numba Compilation")

        cfg = self.cfg

        self.individual_infection_counter = np.zeros(cfg.N_tot, dtype=np.uint16)

        H = simulation_utils.get_hospitalization_variables(cfg)
        (
            H_probability_matrix_csum,
            H_my_state,
            H_agents_in_state,
            H_state_total_counts,
            H_move_matrix_sum,
            H_cumsum_move,
            H_move_matrix_cumsum,
        ) = H

        res = nb_run_simulation(
            cfg.N_tot,
            self.g_total_sum_of_state_changes,
            self.g_cumulative_sum_of_state_changes,
            self.state_total_counts,
            self.agents_in_state,
            self.my_state,
            self.g_cumulative_sum_infection_rates,
            self.N_states,
            self.my_sum_of_rates,
            self.SIR_transition_rates,
            self.N_infectious_states,
            # N_connections,
            self.my_number_of_contacts,
            self.my_rates.array,
            self.my_connections.array,
            # ages,
            self.my_age,
            self.individual_infection_counter,
            H_probability_matrix_csum,
            H_my_state,
            H_agents_in_state,
            H_state_total_counts,
            H_move_matrix_sum,
            H_cumsum_move,
            H_move_matrix_cumsum,
            self.nts,
            self.verbose,
            self.my_connections_type.array,
        )

        out_time, out_state_counts, out_my_state = res

        # res = nb_run_simulation(cfg.N_tot, self.g_total_sum_of_state_changes, self.g_cumulative_sum_of_state_changes, self.state_total_counts, self.agents_in_state, self.my_state, self.g_cumulative_sum_infection_rates, self.N_states, self.my_sum_of_rates, self.SIR_transition_rates, self.N_infectious_states, self.my_number_of_contacts, self.my_rates.array, self.my_connections.array, self.my_connections_type.array, self.my_age, self.individual_infection_counter, self.cs_move_individual, self.nts, self.verbose, self.non_infectable_agents) # agent_is_infectious
        # out_time, out_state_counts, out_my_state = res # out_H_state_total_counts

        track_memory("Arrays Conversion")

        self.time = np.array(out_time)
        self.state_counts = np.array(out_state_counts)
        self.my_state = np.array(out_my_state)
        # self.H_state_total_counts = np.array(out_H_state_total_counts)

    def make_dataframe(self):

        self.track_memory("Make DataFrame")
        self.df = df = simulation_utils.state_counts_to_df(self.time, self.state_counts)

        self.track_memory("Save CSV")
        utils.make_sure_folder_exist(self.filename)
        # save csv file
        df.to_csv(self.filename, index=False)
        return df

    def save_simulation_results(self, save_only_ID_0=False, time_elapsed=None):

        if save_only_ID_0 and self.ID != 0:
            return None

        utils.make_sure_folder_exist(self.filenames["network_network"], delete_file_if_exists=True)

        self.track_memory("Saving HDF5 File")
        with h5py.File(self.filenames["network_network"], "w") as f:  #
            f.create_dataset("coordinates", data=self.coordinates)
            f.create_dataset("coordinate_indices", data=self.coordinate_indices)
            f.create_dataset("my_state", data=self.my_state)
            f.create_dataset("my_number_of_contacts", data=self.my_number_of_contacts)
            f.create_dataset("my_age", data=self.my_age)
            f.create_dataset("cfg_str", data=str(self.cfg))  # import ast; ast.literal_eval(str(cfg))
            f.create_dataset("df", data=utils.dataframe_to_hdf5_format(self.df))

            if time_elapsed:
                f.create_dataset("time_elapsed", data=time_elapsed)

            if self.do_track_memory:
                memory_file = self.filenames["memory"]
                (self.df_time_memory, self.df_change_points,) = simulation_utils.parse_memory_file(memory_file)
                df_time_memory_hdf5 = utils.dataframe_to_hdf5_format(self.df_time_memory, cols_to_str=["ChangePoint"])
                df_change_points_hdf5 = utils.dataframe_to_hdf5_format(self.df_change_points, include_index=True)

                f.create_dataset("memory_file", data=Path(memory_file).read_text())
                f.create_dataset("df_time_memory", data=df_time_memory_hdf5)
                f.create_dataset("df_change_points", data=df_change_points_hdf5)

            # if do_include_awkward:
            #     if do_track_memory:
            #         track_memory('Awkward')
            #     g = awkward0.hdf5(f)
            #     g["my_connections"] = awkward0.fromiter(out_my_connections).astype(np.int32)
            #     g["my_rates"] = awkward0.fromiter(out_my_rates)

            for key, val in self.cfg.items():
                f.attrs[key] = val

        # self.track_memory('Finished')
        # if verbose:
        #     print(f"Run took in total: {t.elapsed:.1f}s.")

        if self.verbose:
            print("\n\n")
            print("coordinates", utils.get_size(self.coordinates))
            print("my_state", utils.get_size(self.my_state))
            print("my_number_of_contacts", utils.get_size(self.my_number_of_contacts))
            print("my_age", utils.get_size(self.my_age))

    def save_memory_figure(self, savefig=True):
        if self.do_track_memory:
            fig, ax = simulation_utils.plot_memory_comsumption(
                self.df_time_memory, self.df_change_points, min_TimeDiffRel=0.1, min_MemoryDiffRel=0.1, time_unit="s",
            )
            if savefig:
                fig.savefig(self.filenames["memory"].replace(".txt", ".pdf"))


#%%


def run_full_simulation(
    filename, verbose=False, force_rerun=False, only_initialize_network=False, save_initial_network=True
):

    with Timer() as t, warnings.catch_warnings():
        if not verbose:
            warnings.simplefilter("ignore", NumbaTypeSafetyWarning)
            warnings.simplefilter("ignore", NumbaExperimentalFeatureWarning)
            warnings.simplefilter("ignore", NumbaPendingDeprecationWarning)

        simulation = Simulation(filename, verbose)
        simulation.initialize_network(force_rerun=force_rerun, save_initial_network=save_initial_network)
        if only_initialize_network:
            return None

        simulation.make_initial_infections()
        simulation.run_simulation()
        simulation.make_dataframe()
        simulation.save_simulation_results(time_elapsed=t.elapsed)
        simulation.save_memory_figure()

        if verbose and simulation.ID == 0:
            print(f"\n\n{simulation.cfg}\n")
            print(simulation.df_change_points)

    if verbose:
        print("\n\nFinished!!!")


reload(utils)
reload(simulation_utils)

verbose = True
force_rerun = False
filename = "Data/ABM/N_tot__58000__N_init__100__N_ages__10__mu__40.0__sigma_mu__0.0__beta__0.01__sigma_beta__0.0__rho__0.0__lambda_E__1.0__lambda_I__1.0__epsilon_rho__0.01__beta_scaling__1.0__age_mixing__1.0__algo__2/N_tot__58000__N_init__100__N_ages__1__mu__40.0__sigma_mu__0.0__beta__0.01__sigma_beta__0.0__rho__0.0__lambda_E__1.0__lambda_I__1.0__epsilon_rho__0.01__beta_scaling__1.0__age_mixing__1.0__algo__2__ID__000.csv"
# filename = filename.replace('ID__000', 'ID__001')
# filename = filename.replace('N_tot__58000', 'N_tot__10000')
filename = filename.replace("N_tot__58000", "N_tot__100000")


# if running just til file
if Path("").cwd().stem == "src":

    with Timer() as t:
        simulation = Simulation(filename, verbose)
        simulation.initialize_network(force_rerun=force_rerun)
        simulation.make_initial_infections()
        simulation.run_simulation()
        df = simulation.make_dataframe()
        display(df)
        # simulation.save_simulation_results(time_elapsed=t.elapsed)
        # simulation.save_memory_figure()

    # if False:
    #     my_number_of_contacts = simulation.my_number_of_contacts
    #     # counter_ages = simulation.counter_ages
    #     agents_in_age_group = simulation.agents_in_age_group
    #     fig, ax = plt.subplots(figsize=(10, 6))
    #     for i, agents in enumerate(agents_in_age_group):
    #         ax.hist(my_number_of_contacts[agents], 50, label=i, histtype='step', lw=2)
    #     ax.legend()

    # if False:

    #     import pyarrow as pa
    #     import pyarrow.parquet as pq

    #     x = List()
    #     x.append(np.arange(2))
    #     x.append(np.arange(3))
    #     y = ak.from_iter(x)
    #     ak.to_parquet(x, "x.parquet")
    #     ak.to_parquet(y, "y.parquet")

    #     arrow_x = ak.to_arrow(x)
    #     arrow_y = ak.to_arrow(y)

    #     table_x = pq.read_table('x.parquet')
    #     table_y = pq.read_table('y.parquet')

    #     table_x.to_pandas()
    #     table_y.to_pandas()

    #     table_x.to_pydict()

    # pa.Table.from_pydict({'x': arrow_x, 'y': })
