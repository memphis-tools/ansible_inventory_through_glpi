"""The script generates a formated ini inventory"""

import json
import pandas as pd
import yaml


DATAFRAME = ""
ROLE_VARS = ""
# DATAFRAME_WANTED_KEYS_DICT: GLPI's JSON expected keys.
DATAFRAME_WANTED_KEYS_DICT = {
    "1": "hostname",
    "3": "location",
    "31": "status",
    "45": "OS name",
    "46": "OS version",
    "71": "Group",
    "126": "Networking ip",
    "205": "Domains name",
    "901": "GLPI agent's tags",
}
DATAFRAME_WANTED_KEYS_LIST = DATAFRAME_WANTED_KEYS_DICT.keys()
EXCLUDED_HOSTS_LIST = []
TEMP_LIST = []


class ValidationError(Exception):
    """
    Description: custom exception to raise if GLPI's JSON malformed
    """


def get_ansible_role_vars():
    """
    Description: get vars defined in the role's vars.
    """
    with open("roles/make_inventory/vars/main.yml", encoding="utf-8") as fd:
        role_vars = yaml.safe_load(fd)
    return role_vars


def make_excluded_hosts_list(hosts_to_exclude_from_inventory_file_path):
    """
    Description: loads in a list the hosts to exclude from the inventory.
    """
    with open(hosts_to_exclude_from_inventory_file_path, "r", encoding="utf-8") as fd:
        temp = fd.readlines()[1:]
    for hostname in temp:
        EXCLUDED_HOSTS_LIST.append(str(hostname).rstrip("\n").lower())


def import_glpi_json_data(temp_glpi_computers_output_file_path):
    """
    Description: we load the GLPI's API response into JSON.
    """
    with open(temp_glpi_computers_output_file_path, "r", encoding="utf-8") as fd:
        json_data = json.loads(fd.read())
    user_sample_host_keys = set(json_data["data"][0].keys())
    dataframe_expected_keys = set(DATAFRAME_WANTED_KEYS_DICT.keys())
    if dataframe_expected_keys.issubset(user_sample_host_keys):
        return json_data
    raise ValidationError(
        "You have not set the GLPI user's view as expected by the script."
    )


def update_ansible_inventory_path(a_list):
    """
    Description: insert element into the ansible ini inventory.
    """
    with open(
        ROLE_VARS["make_inventory_temp_ini_inventory_output_file_path"],
        "a",
        encoding="utf-8",
    ) as fd:
        for e in a_list:
            fd.write(f"{e}\n")


def drop_unaccepted_columns(df):
    """
    Description: remove unnecessary columns from the dataframe.
    """
    for column in df.columns:
        if column not in DATAFRAME_WANTED_KEYS_LIST:
            del df[column]


def drop_unused_additional_columns(df):
    """
    Description: remove unused columns from the dataframe once formated.
    """
    for column in ["46", "major_version", "minor_version"]:
        del df[column]


def load_json_then_create_and_format_dataframe(json_data):
    """
    Description: create a panda dataframe with GLPI's JSON.
    """
    df = pd.json_normalize(json_data, record_path="data")
    print("[INFO][1] INITIAL DATAFRAME")
    print(f"{df}")
    drop_unaccepted_columns(df)
    for key in DATAFRAME_WANTED_KEYS_DICT:
        if key not in ("126", "205"):
            df[key] = df[key].str.lower()
    # Location: we replace the blank with "_".
    df["3"] = df["3"].str.replace(" ", "_")
    # OS name: we want the distribution name
    df["45"] = df["45"].str.extract(r"(\w+)")
    # OS version: we need to replace "."
    df["46"] = df["46"].str.replace(".", "_")
    # GLPI group: in case of a child group
    # devops_team > sys_admin_linux: we want sys_admin_linux
    df["71"] = df["71"].str.replace(r"(\w+ > )", "", regex=True)
    # IPS: we want the third entry from the list
    df["126"] = pd.Series((i[2] for i in df["126"])).str.replace(" ", "")
    # Domains: if multiple domains we grab the first one.
    df["205"] = df["205"].astype(str).str.extract(r"([\w\.]+)")
    # We add a temp column to the dataframe relative to the OS major version (example: "7")
    df["major_version"] = df["46"].astype(str).str.extract(r"(\d+)")
    # We add a temp column to the dataframe relative to the OS minor version (example: "7_5")
    df["minor_version"] = df["46"].astype(str).str.extract(r"(\d+\_\d+)")
    # We add a column to the dataframe relative to the OS major version (example: "redhat_7")
    df["os_major"] = df["45"].astype(str) + "_" + df["major_version"]
    # We add a column to the dataframe relative to the OS minor version (example: "redhat_7_5")
    df["os_minor"] = df["45"].astype(str) + "_" + df["minor_version"]
    # Particular case for OS without a minor version
    # example we have a CentOS with version 8 instead of 8.0, so we force it to obtain "centos_8_0".
    df["os_minor"] = df["os_minor"].fillna(df["os_major"].astype(str) + "_0")
    drop_unused_additional_columns(df)
    print("[INFO][2] FORMATED DATAFRAME")
    print(f"{df}")
    return df


def record_each_linux_distribution(df):
    """
    Description: we set the main parent ansible group.
    """
    # update_ansible_inventory_path(["[linux:children]"])
    TEMP_LIST.append("[linux:children]")
    for os_name_and_version in df["45"].unique():
        os_name = os_name_and_version.lower().split(" ")[0]
        if os_name not in TEMP_LIST:
            TEMP_LIST.append(os_name)
    TEMP_LIST.append("")


def record_each_child_by_os_major_version(df):
    """
    Description: we set a group for each OS major version children.
    """
    for os_name_and_version in df["45"].unique():
        TEMP_LIST.append(f"[{os_name_and_version}:children]")
        for h in df[df["os_major"].str.contains(os_name_and_version)].index:
            if df.loc[h]["os_major"] not in TEMP_LIST:
                TEMP_LIST.append(str(df.loc[h]["os_major"]))
        TEMP_LIST.append("")


def record_each_child_by_os_minor_version(df):
    """
    Description: we set a group for each OS minor version children.
    """
    for d in df["os_major"].unique():
        TEMP_LIST.append(f"[{d}:children]")
        for h in df[df["os_major"].str.contains(d)].index:
            if df.loc[h]["os_minor"] not in TEMP_LIST:
                TEMP_LIST.append(str(df.loc[h]["os_minor"]))
        TEMP_LIST.append("")


def get_host_datas_from_hostname(hostname):
    """
    Description: used by function record_each_host_by_fusioninventory_tag.
    When we browse the tags we need to get the host's ip and domain.
    """
    for h in DATAFRAME[DATAFRAME["1"] == hostname].index:
        if DATAFRAME.loc[h]["205"] not in DATAFRAME.loc[h]["1"]:
            return (
                f".{DATAFRAME.loc[h]['205']}"
                + " ansible_host="
                + DATAFRAME.loc[h]["126"]
            )
        return " ansible_host=" + DATAFRAME.loc[h]["126"]


def record_each_host_by_fusioninventory_tag(df):
    """
    Description: we parse all tags. We bind host to tag.
    """
    # 1] on crée un "set" avec tous les tags
    temp_tags_lists = df["901"].unique()
    temp_tags_set = set()
    for d in temp_tags_lists:
        for f in d.split():
            temp_tags_set.add(f)
    # 2] on boucle sur le "set" des tags et pour chacun on lie les hotes.
    for d in temp_tags_set:
        header_switch = False
        for h in df[df["901"].str.contains(d)]["1"]:
            if header_switch is False:
                TEMP_LIST.append(f"[{d}]")
                header_switch = True
            if h not in EXCLUDED_HOSTS_LIST:
                get_host_datas_from_hostname(h)
                TEMP_LIST.append(h + get_host_datas_from_hostname(h))
        TEMP_LIST.append("")


def record_each_host_by_standard_key(df, key):
    """
    Description:

    Parameters:
    df -- the dataframe
    key -- integer, relative to a dataframe column label
    """
    for d in df[key].unique():
        TEMP_LIST.append(f"[{d.lower()}]")
        for h in df[df[key] == d].index:
            if df.loc[h]["1"] not in EXCLUDED_HOSTS_LIST:
                if df.loc[h]["205"] not in df.loc[h]["1"]:
                    TEMP_LIST.append(
                        df.loc[h]["1"]
                        + f".{df.loc[h]['205']}"
                        + " ansible_host="
                        + df.loc[h]["126"]
                    )
                else:
                    TEMP_LIST.append(
                        df.loc[h]["1"] + " ansible_host=" + df.loc[h]["126"]
                    )
        TEMP_LIST.append("")
    TEMP_LIST.append("")


ROLE_VARS = get_ansible_role_vars()
glpi_json_data = import_glpi_json_data(
    ROLE_VARS["make_inventory_temp_glpi_computers_output_file_path"]
)
DATAFRAME = load_json_then_create_and_format_dataframe(glpi_json_data)
make_excluded_hosts_list(
    ROLE_VARS["make_inventory_hosts_to_exclude_from_inventory_file_path"]
)
record_each_linux_distribution(DATAFRAME)
record_each_child_by_os_major_version(DATAFRAME)
record_each_host_by_standard_key(DATAFRAME, "os_major")
record_each_child_by_os_minor_version(DATAFRAME)
record_each_host_by_standard_key(DATAFRAME, "os_minor")
record_each_host_by_fusioninventory_tag(DATAFRAME)
record_each_host_by_standard_key(DATAFRAME, "3")
record_each_host_by_standard_key(DATAFRAME, "31")
record_each_host_by_standard_key(DATAFRAME, "71")
update_ansible_inventory_path(TEMP_LIST)
