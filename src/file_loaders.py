import pandas as pd
from pathlib import Path


def pandas_load_file(file, return_only_df=False):
    df_raw = pd.read_csv(file).convert_dtypes()

    for state in ['E', 'I']:
        df_raw[state] = sum((df_raw[col] for col in df_raw.columns if state in col and len(col) == 2))

    # only keep relevant columns
    df = df_raw[['Time', 'E', 'I', 'R']]
    df.rename(columns={'Time': 'time'}, inplace=True)
    return df



def path(file):
    if isinstance(file, str):
        file = Path(file)
    return file

def file_is_empty(file):
    return (path(file).stat().st_size == 0)


def get_all_ABN_files(base_dir='Data/ABN'):
    "get all csv ABN result files"
    files = path(base_dir).rglob(f'*.csv')
    return sorted([file for file in files if not file_is_empty(file)])

def get_ABN_parameters(files):
    return list(dict.fromkeys((path(file).parent.name for file in files)))


class ABNFiles:

    def __init__(self, base_dir='Data/ABN'):
        self.base_dir = Path(base_dir)
        self.all_files = get_all_ABN_files(base_dir)
        self.ABN_parameters = self.keys = get_ABN_parameters(self.all_files)
        self.d = self._convert_all_files_to_dict()

    def _convert_all_files_to_dict(self):
        """converts all files to a dict where the simulation parameter is the key
        and the value is a set of all files in that folder"""
        d = {}
        for ABN_parameter in self.ABN_parameters:
            d[ABN_parameter] = list((self.base_dir/ABN_parameter).rglob('*.csv'))
        return d

    def __iter__(self):
        for file in self.all_files:
            yield file

    def iter_files(self):
        for file in self.all_files:
            yield file

    def iter_folders(self):
        return self.ABN_parameters

    def __getitem__(self, key):
        if isinstance(key, str):
            return self.d[key]
        elif isinstance(key, int):
            return self.all_files[key]

    def __len__(self):
        return len(self.all_files)