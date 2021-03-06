import logging
from abc import abstractmethod

import dask.dataframe as dd
import pandas as pd
from Bio import SeqIO

import openomics
from openomics.utils.read_gtf import read_gtf
from .base import Dataset


# from gtfparse import read_gtf

class SequenceDataset(Dataset):
    def __init__(self, replace_U2T=False, **kwargs):
        """
        Args:
            replace_U2T:
            **kwargs:
        """
        self.replace_U2T = replace_U2T

        super(SequenceDataset, self).__init__(**kwargs)

    @abstractmethod
    def read_fasta(self, fasta_file, replace_U2T, npartitions=None):
        """Returns a pandas DataFrame containing the fasta sequence entries.
        With a column named 'sequence'. :param npartitions: :param replace_U2T:
        :param fasta_file: path to the fasta file, usually as
        self.file_resources[<file_name>] :type fasta_file: str

        Args:
            fasta_file:
            replace_U2T:
            npartitions:
        """
        raise NotImplementedError

    @abstractmethod
    def get_sequences(self, index, omic, agg_sequences, **kwargs):
        """Returns a dictionary where keys are 'index' and values are
        sequence(s). :param index: {"gene_id", "gene_name", "transcript_id",
        "transcript_name"}

            The index

        Args:
            index:
            omic (str): {"lncRNA", "microRNA", "messengerRNA"}
            agg_sequences (str): {"all", "shortest", "longest"}
            **kwargs: any additional argument to pass to
                SequenceDataset.get_sequences()
        """
        raise NotImplementedError

    def get_aggregator(self, agg=None):
        """
        Args:
            agg:
        """
        if agg == "all":
            agg_func = lambda x: list(x) if not isinstance(x, str) else x
        elif agg == "shortest":
            agg_func = lambda x: min(x, key=len)
        elif agg == "longest":
            agg_func = lambda x: max(x, key=len)
        else:
            raise Exception("agg_sequences argument must be one of {'all', 'shortest', 'longest'}")
        return agg_func


class GENCODE(SequenceDataset):
    def __init__(self, path="ftp://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_32/",
                 file_resources=None, col_rename=None, npartitions=0,
                 replace_U2T=True, remove_version_num=False):
        """
        Args:
            path:
            file_resources:
            col_rename:
            npartitions:
            replace_U2T:
            remove_version_num:
        """
        if file_resources is None:
            file_resources = {"long_noncoding_RNAs.gtf": "gencode.v32.long_noncoding_RNAs.gtf.gz",
                              "basic.annotation.gtf": "gencode.v32.basic.annotation.gtf.gz",
                              "lncRNA_transcripts.fa": "gencode.v32.lncRNA_transcripts.fa.gz",
                              "transcripts.fa": "gencode.v32.transcripts.fa.gz"}

        self.remove_version_num = remove_version_num

        super(GENCODE, self).__init__(path=path, file_resources=file_resources, col_rename=col_rename,
                                      replace_U2T=replace_U2T, npartitions=npartitions)

    def load_dataframe(self, file_resources, npartitions=None):
        """
        Args:
            file_resources:
            npartitions:
        """
        dfs = []
        for gtf_file in file_resources:
            if '.gtf' in gtf_file:
                df = read_gtf(file_resources[gtf_file], npartitions=npartitions)
                dfs.append(df)

        if npartitions:
            annotation_df = dd.concat(dfs)
        else:
            annotation_df = pd.concat(dfs)

        if self.remove_version_num:
            annotation_df['gene_id'] = annotation_df['gene_id'].str.replace("[.].*", "")
            annotation_df['transcript_id'] = annotation_df['transcript_id'].str.replace("[.].*", "")

        return annotation_df

    def read_fasta(self, fasta_file, replace_U2T, npartitions=None):
        """
        Args:
            fasta_file:
            replace_U2T:
            npartitions:
        """
        entries = []
        for record in SeqIO.parse(fasta_file, "fasta"):
            record_dict = {"gene_id": record.id.split("|")[1],
                           "gene_name": record.id.split("|")[5],
                           "transcript_id": record.id.split("|")[0],
                           "transcript_name": record.id.split("|")[4],
                           "transcript_length": record.id.split("|")[6],
                           "transcript_biotype": record.id.split("|")[7],
                           "sequence": str(record.seq), }

            entries.append(record_dict)

        entries_df = pd.DataFrame(entries)
        if npartitions:
            entries_df = dd.from_pandas(entries_df)

        if replace_U2T:
            entries_df["sequence"] = entries_df["sequence"].str.replace("U", "T")
        if self.remove_version_num:
            entries_df['gene_id'] = entries_df['gene_id'].str.replace("[.].*", "")
            entries_df['transcript_id'] = entries_df['transcript_id'].str.replace("[.].*", "")
        return entries_df

    def get_sequences(self, index, omic, agg_sequences, biotypes=None):
        # type: (str, str, str, List[str]) -> None
        """
        Args:
            index:
            omic:
            agg_sequences:
            biotypes:
        """
        agg_func = self.get_aggregator(agg_sequences)

        # Parse lncRNA & mRNA fasta
        if omic == openomics.MessengerRNA.name():
            fasta_file = self.file_resources["transcripts.fa"]
        elif omic == openomics.LncRNA.name():
            fasta_file = self.file_resources["lncRNA_transcripts.fa"]
        else:
            raise Exception("omic argument must be one of {'MessengerRNA', 'LncRNA'}")

        entries_df = self.read_fasta(fasta_file, self.replace_U2T)

        if "gene" in index:
            if biotypes:
                entries_df = entries_df[entries_df["transcript_biotype"].isin(biotypes)]
            else:
                print("INFO: You can pass in a list of transcript biotypes to filter using the argument 'biotypes'.")

            return entries_df.groupby(index)["sequence"].agg(agg_func)
        elif "transcript" in index:
            return entries_df.groupby(index)["sequence"].first()
        else:
            raise Exception(
                "The level argument must be one of {'gene_id', 'transcript_id', or 'gene_name', or 'transcript_name'}")

    def get_rename_dict(self, from_index='gene_id', to_index='gene_name'):
        """
        Args:
            from_index:
            to_index:
        """
        ensembl_id_to_gene_name = pd.Series(self.data[to_index].values,
                                            index=self.data[from_index]).to_dict()
        return ensembl_id_to_gene_name


class MirBase(SequenceDataset):
    def __init__(self, path="ftp://mirbase.org/pub/mirbase/CURRENT/",
                 sequence="hairpin", species="Homo sapiens", species_id=9606,
                 file_resources=None, col_rename=None,
                 npartitions=0, replace_U2T=True):
        """
        Args:
            path:
            sequence:
            species (int): Species code, e.g., 9606 for human
            species_id:
            file_resources:
            col_rename:
            npartitions:
            replace_U2T:
        """
        if file_resources is None:
            file_resources = {}
            file_resources["aliases.txt"] = "aliases.txt.gz"
            file_resources["mature.fa"] = "mature.fa.gz"
            file_resources["hairpin.fa"] = "hairpin.fa.gz"
            file_resources["rnacentral.mirbase.tsv"] = \
                "ftp://ftp.ebi.ac.uk/pub/databases/RNAcentral/current_release/id_mapping/database_mappings/mirbase.tsv"

        self.sequence = sequence
        self.species_id = species_id
        self.species = species
        super(MirBase, self).__init__(path=path, file_resources=file_resources, col_rename=col_rename,
                                      npartitions=npartitions, replace_U2T=replace_U2T)

    def load_dataframe(self, file_resources, npartitions=None):
        """
        Args:
            file_resources:
            npartitions:
        """
        rnacentral_mirbase = pd.read_table(file_resources["rnacentral.mirbase.tsv"], low_memory=True, header=None,
                                           names=["RNAcentral id", "database", "mirbase id", "species", "RNA type",
                                                  "NA"])

        rnacentral_mirbase = rnacentral_mirbase.set_index("mirbase id")
        rnacentral_mirbase["species"] = rnacentral_mirbase["species"].astype("O")
        if self.species_id is not None:
            rnacentral_mirbase = rnacentral_mirbase[rnacentral_mirbase["species"] == self.species_id]

        mirbase_aliases = pd.read_table(file_resources["aliases.txt"], low_memory=True, header=None,
                                        names=["mirbase id", "gene_name"], dtype="O").set_index("mirbase id")
        mirbase_aliases = mirbase_aliases.join(rnacentral_mirbase, how="inner")

        # Expanding miRNA names in each MirBase Ascension ID
        mirna_names = mirbase_aliases \
            .apply(lambda x: pd.Series(x['gene_name'].split(";")[:-1]), axis=1) \
            .stack() \
            .reset_index(level=1, drop=True)

        mirna_names.name = "gene_name"

        if npartitions:
            mirbase_aliases = dd.from_pandas(mirbase_aliases, npartitions=npartitions)

        mirbase_aliases = mirbase_aliases.drop('gene_name', axis=1).join(mirna_names)

        # mirbase_name["miRNA name"] = mirbase_name["miRNA name"].str.lower()
        # mirbase_name["miRNA name"] = mirbase_name["miRNA name"].str.replace("-3p.*|-5p.*", "")

        return mirbase_aliases

    def read_fasta(self, fasta_file, replace_U2T, npartitions=None):
        """
        Args:
            fasta_file:
            replace_U2T:
            npartitions:
        """
        entries = []
        for record in SeqIO.parse(fasta_file, "fasta"):
            record_dict = {"gene_id": record.id,
                           "gene_name": str(record.name),
                           "mirbase id": record.description.split(" ")[1],
                           "mir_name": record.description.split(" ")[5],
                           "species": " ".join(record.description.split(" ")[2:4]),
                           "sequence": str(record.seq),
                           }

            entries.append(record_dict)

        entries_df = pd.DataFrame(entries)
        if npartitions:
            entries_df = dd.from_pandas(entries_df)

        if replace_U2T:
            entries_df["sequence"] = entries_df["sequence"].str.replace("U", "T")
        return entries_df

    def get_sequences(self, index="gene_name", omic=None, agg_sequences="all"):
        """
        Args:
            index:
            omic:
            agg_sequences:
        """
        if hasattr(self, "seq_dict"):
            logging.info("Using cached sequences dict")
            return self.seq_dict

        if self.sequence == "hairpin":
            file = self.file_resources["hairpin.fa"]
        elif self.sequence == "mature":
            file = self.file_resources["mature.fa"]
        else:
            raise Exception("sequence must be either 'hairpin' or 'mature'")

        fasta_df = self.read_fasta(file, self.replace_U2T)

        self.seq_dict = fasta_df.set_index(index)["sequence"].agg(self.get_aggregator(agg_sequences))

        return self.seq_dict
