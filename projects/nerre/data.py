import os
from collections import defaultdict
from typing import NamedTuple, Sequence, Mapping, Tuple

# load training, dev and test data
from jtr.preprocess.batch import get_batches
from jtr.preprocess.map import deep_map
from jtr.preprocess.vocab import Vocab
import numpy as np

train_dir = "/Users/riedel/corpora/scienceie/train2"
dev_dir = "/Users/riedel/corpora/scienceie/dev/"

Token = NamedTuple("Token", [("token_start", int),
                             ("token_end", int),
                             ("word", str),
                             ("index", int)])

Sentence = NamedTuple("Sentence", [("tokens", Sequence[Token])])

Keyphrase = NamedTuple("KeyPhrase", [("start", int), ("end", int), ("type", str), ("text", str)])

Instance = NamedTuple("Instance", [("text", str),
                                   ("doc", Sequence[Sentence]),
                                   ("labels", Mapping[Keyphrase, Sequence[Token]]),
                                   ("file_name", str),
                                   ("relations", Sequence[Tuple[str, Keyphrase, Keyphrase]])])

bio_vocab = Vocab()
bio_vocab("B")
bio_vocab("I")
bio_vocab("O")
bio_vocab.freeze()

label_vocab = Vocab()
label_vocab("Material")
label_vocab("Process")
label_vocab("Task")
label_vocab.freeze()

rel_type_vocab = Vocab()
rel_type_vocab("HYPONYM_OF")
rel_type_vocab("SYNONYM_OF")
rel_type_vocab("O")
rel_type_vocab.freeze()


def read_ann(textfolder=dev_dir):
    '''
    Read .ann files and look up corresponding spans in .txt files
    :param textfolder:
    :return: tokens with character offsets
    '''
    word_types = set()
    from nltk import sent_tokenize, word_tokenize
    flist = os.listdir(textfolder)
    instances = []
    for f in flist:
        if not f.endswith(".ann"):
            continue
        f_anno = open(os.path.join(textfolder, f), "rU")
        f_text = open(os.path.join(textfolder, f.replace(".ann", ".txt")), "rU")

        # there's only one line, as each .ann file is one text paragraph
        for l in f_text:
            text = l

        sents = sent_tokenize(text)
        offset_to_token = {}
        doc = []
        for s in sents:
            tokens = word_tokenize(s)
            word_types.update(tokens)
            # recover spans for each token
            current_token_index = 0
            char_index = 0
            within_token_index = 0
            token = tokens[current_token_index]
            result_tokens = []
            start_offset = 0
            # print(tokens)
            while char_index < len(text):
                while within_token_index < len(token) and text[char_index] == token[within_token_index]:
                    char_index += 1
                    within_token_index += 1
                if within_token_index == len(token):
                    rich_token = Token(start_offset, char_index, token, len(result_tokens))
                    result_tokens.append(rich_token)
                    for offset in range(start_offset, char_index):
                        offset_to_token[offset] = rich_token
                    if current_token_index < len(tokens) - 1:
                        current_token_index += 1
                        token = tokens[current_token_index]
                    else:
                        char_index = len(text)
                else:
                    char_index += 1

                within_token_index = 0
                start_offset = char_index
            assert len(tokens) == len(result_tokens)
            assert [t.word for t in result_tokens] == tokens
            doc.append(Sentence(result_tokens))

        # mapping from tokens to their labels
        token_to_labels = defaultdict(set)
        keyphrase_to_tokens = defaultdict(set)
        relations = []
        id_to_keyphrase = {}

        resolved_relations = []

        for l in f_anno:
            anno_inst = l.strip("\n").split("\t")
            if len(anno_inst) == 3:
                anno_inst1 = anno_inst[1].split(" ")
                annotation_id = anno_inst[0].strip()
                if len(anno_inst1) == 3:
                    keytype, start, end = anno_inst1
                else:
                    keytype, start, _, end = anno_inst1
                if not keytype.endswith("-of"):

                    # look up span in text and print error message if it doesn't match the .ann span text
                    keyphr_text_lookup = text[int(start):int(end)]
                    keyphr_ann = anno_inst[2]
                    keyphrase = Keyphrase(int(start), int(end), keytype, keyphr_text_lookup)
                    id_to_keyphrase[annotation_id] = keyphrase
                    assert keyphr_text_lookup == keyphr_ann
                    for offset in range(int(start), int(end)):
                        token = offset_to_token.get(offset, None)
                        if token:
                            token_to_labels[token].add(keytype)
                            keyphrase_to_tokens[keyphrase].add(token)
                else:
                    relations.append((annotation_id, anno_inst1))
        for annotation_id, (rel, arg1, arg2) in relations:
            arg1_id = arg1.split(":")[1]
            arg2_id = arg2.split(":")[1]
            arg1_kp = id_to_keyphrase[arg1_id]
            arg2_kp = id_to_keyphrase[arg2_id]
            resolved_relations.append((rel, arg1_kp, arg2_kp))

        sorted_kp_to_tokens = {}
        for kp, tokens in keyphrase_to_tokens.items():
            sorted_kp_to_tokens[kp] = sorted(tokens, key=lambda t: t.token_start)

        instances.append(Instance(text, doc, sorted_kp_to_tokens, f, resolved_relations))
        # print(Instance(doc, keyphrase_to_tokens))
    print("Collected {} word types".format(len(word_types)))
    return instances


def convert_to_batchable_format(instances, vocab,
                                sentences_as_ints_ph="sentences_as_ints",
                                document_indices_ph="document_indices",
                                bio_labels_as_ints_ph="bio_labels_as_ints",
                                type_labels_as_ints_ph="type_labels_as_ints",
                                token_char_offsets_ph="token_char_offsets",
                                relation_matrices_ph="relation_matrices",
                                sentence_lengths_ph="sentence_length"):
    # convert
    sentences_as_ints = []
    bio_labels_as_ints = []
    type_labels_as_ints = []
    document_indices = []
    token_char_offsets = []
    relation_matrices = []
    sentence_lengths = []
    for doc_index, instance in enumerate(instances):
        for sentence in instance.doc:
            sentences_as_ints.append([vocab(token.word) for token in sentence.tokens])
            token_char_offsets.append([[token.token_start, token.token_end] for token in sentence.tokens])
            document_indices.append(doc_index)
            sentence_lengths.append(len(sentence.tokens))
            bio_labels = [bio_vocab("O")] * len(sentence.tokens)
            type_labels = [label_vocab("O")] * len(sentence.tokens)
            sentence_tokens = set(sentence.tokens)

            for kp, kp_tokens in instance.labels.items():
                started = False
                for token in sentence.tokens:
                    if token in kp_tokens:
                        type_labels[token.index] = label_vocab(kp.type)
                        if not started:
                            bio_labels[token.index] = bio_vocab("B")
                            started = True
                        else:
                            bio_labels[token.index] = bio_vocab("I")
            bio_labels_as_ints.append(bio_labels)
            type_labels_as_ints.append(type_labels)

            relation_matrix = np.ndarray([len(sentence.tokens), len(sentence.tokens)], dtype=np.int32)
            relation_matrix.fill(rel_type_vocab("O"))

            for rel, arg1, arg2 in instance.relations:
                if instance.labels[arg1][0] in sentence_tokens and instance.labels[arg2][0] in sentence_tokens:
                    tok1 = instance.labels[arg1][0]
                    tok2 = instance.labels[arg2][0]
                    relation_matrix[tok1.index, tok2.index] = rel_type_vocab(rel)
            relation_matrices.append(relation_matrix.tolist())

    return {
        sentences_as_ints_ph: sentences_as_ints,
        document_indices_ph: document_indices,
        bio_labels_as_ints_ph: bio_labels_as_ints,
        type_labels_as_ints_ph: type_labels_as_ints,
        token_char_offsets_ph: token_char_offsets,
        relation_matrices_ph: relation_matrices,
        sentence_lengths_ph: sentence_lengths

    }


def fill_vocab(instances, vocab):
    for instance in instances:
        for sent in instance.doc:
            for token in sent.tokens:
                vocab(token.word)


def convert_batch_to_ann(batch, instances,
                         sentences_as_ints_key="sentences_as_ints",
                         document_indices_key="document_indices",
                         bio_labels_key="bio_labels_as_ints",
                         type_labels_as_ints_key="type_labels_as_ints",
                         token_char_offsets_key="token_char_offsets",
                         sentence_lengths_key="sentence_length"):
    doc_id_to_doc_info = {}
    doc_ids = batch[document_indices_key]
    bio_labels = batch[bio_labels_key]
    type_labels = batch[type_labels_as_ints_key]
    sentences = batch[sentences_as_ints_key]
    token_char_offsets = batch[token_char_offsets_key]
    sentence_lengths = batch[sentence_lengths_key]

    for elem_index, doc_id in enumerate(doc_ids):
        instance = instances[doc_id]
        current_kps = doc_id_to_doc_info.get(instance.file_name, {})
        current_kp_start = 0
        current_kp_end = -1
        in_kp = False
        last_symbol = "O"
        kp_type = None
        sentence_length = sentence_lengths[elem_index]
        print(instance.file_name)
        print(instance)

        def create_kp():
            text = instance.text[current_kp_start:current_kp_end]
            kp_id = "T" + str(len(current_kps) + 1)
            kp = Keyphrase(current_kp_start, current_kp_end, kp_type, text)
            current_kps[kp_id] = kp

        bio_label_sequence = bio_labels[elem_index]
        type_label_sequence = type_labels[elem_index]
        token_char_offset_sequence = token_char_offsets[elem_index]
        for bio_label, type_label, (start, end) in zip(bio_label_sequence[:sentence_length],
                                                       type_label_sequence[:sentence_length],
                                                       token_char_offset_sequence[:sentence_length]):
            bio_label_symbol = bio_vocab.get_sym(bio_label)
            type_label_symbol = label_vocab.get_sym(type_label)

            if bio_label_symbol == "B":
                if in_kp:
                    create_kp()
                else:
                    in_kp = True
                    current_kp_start = start
                    kp_type = type_label_symbol
            elif bio_label_symbol == "O":
                if last_symbol != bio_label_symbol:
                    create_kp()
                    in_kp = False

            current_kp_end = end
            last_symbol = bio_label_symbol
        if last_symbol != "O":
            create_kp()

        print(current_kps)

        doc_id_to_doc_info[instance.file_name] = current_kps

    for file_name, keyphrases in doc_id_to_doc_info.items():
        with open("/tmp/" + file_name, "w") as ann:
            for key, kp in keyphrases.items():
                ann.write("{key}\t{label} {start} {end}\t{text}\n".format(key=key,
                                                                          label=kp.type,
                                                                          start=kp.start,
                                                                          end=kp.end,
                                                                          text=kp.text))


if __name__ == "__main__":
    vocab = Vocab()
    instances = read_ann(dev_dir)
    fill_vocab(instances, vocab)
    batchable = convert_to_batchable_format(instances[:2], vocab)
    print(batchable)
    batches = list(get_batches(batchable))[:2]
    for batch in batches:
        print(convert_batch_to_ann(batch, instances))

# print(instances[0].labels)
