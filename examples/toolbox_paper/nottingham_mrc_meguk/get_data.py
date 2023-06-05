"""Get training data.

This script will download source reconstructed resting-state (eyes open)
MEG data.

This data is part of the MRC MEGUK dataset. It is CTF data from the
Nottingham site. 65 subjects are part of this dataset.
"""

import os

# We will download example data hosted on osf.io/by2tc.
# Note, osfclient must be installed. This can be installed with pip:
#
#     pip install osfclient


def get_data(name, output_dir):
    if os.path.exists(output_dir):
        print(f"{output_dir} already downloaded. Skipping..")
        return
    os.system(f"osf -p by2tc fetch data/{name}.zip")
    os.system(f"unzip -o {name}.zip -d {output_dir}")
    os.remove(f"{name}.zip")
    print(f"Data downloaded to: {output_dir}")


# Download the dataset (approximately 700 MB)
#
# This will unzip the notts_mrc_meguk.zip file into a
# directory called "training_data"
get_data("notts_mrc_meguk", output_dir="training_data")
