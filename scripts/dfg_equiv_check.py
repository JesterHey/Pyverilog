from __future__ import annotations
from __future__ import print_function

import sys
import os
from optparse import OptionParser

from pyverilog.dataflow.dataflow_analyzer import VerilogDataflowAnalyzer
from pyverilog.dataflow.optimizer import VerilogDataflowOptimizer, VerilogOptimizer
from pyverilog.dataflow.merge import VerilogDataflowMerge
from pyverilog.dataflow import reorder
from pyverilog.dataflow.dataflow import Bind as DFBind


def _canon_tree(tree, optimizer, do_reorder=True):
    if tree is None:
        return 'None'
    try:
        if do_reorder:
            tree = reorder.reorder(tree)
        if optimizer is not None:
            tree = optimizer.optimize(tree)
        # one more reorder to normalize structure after optimization
        if do_reorder:
            tree = reorder.reorder(tree)
        return tree.tostr()
    except Exception as e:
        return 'ERROR:%s' % (e,)


def _canon_bind(bind, optimizer, do_reorder=True):
    tstr = _canon_tree(bind.tree, optimizer, do_reorder)
    # include dest/msb/lsb/ptr in signature, normalized via optimizer
    parts = ['(Bind']
    if bind.dest is not None:
        parts.append(' dest:%s' % str(bind.dest))
    try:
        msb = optimizer.optimize(bind.msb) if bind.msb is not None else None
        lsb = optimizer.optimize(bind.lsb) if bind.lsb is not None else None
        ptr = optimizer.optimize(bind.ptr) if bind.ptr is not None else None
    except Exception:
        msb, lsb, ptr = bind.msb, bind.lsb, bind.ptr
    if msb is not None:
        parts.append(' msb:' + msb.tostr())
    if lsb is not None:
        parts.append(' lsb:' + lsb.tostr())
    if ptr is not None:
        parts.append(' ptr:' + ptr.tostr())
    parts.append(' tree:' + tstr + ')')
    return ''.join(parts)


def main():
    usage = 'Usage: python scripts/dfg_equiv_check.py -t TOP file.v [more.v]'
    opt = OptionParser(usage=usage)
    opt.add_option('-t', '--top', dest='top', default='TOP', help='Top module name')
    opt.add_option('-I', '--include', dest='include', action='append', default=[], help='Include path')
    opt.add_option('-D', dest='define', action='append', default=[], help='Macro definition')
    opt.add_option('--noreorder', action='store_true', dest='noreorder', default=False, help='Do not reorder before compare')
    (options, args) = opt.parse_args()

    if not args:
        print(usage)
        sys.exit(2)
    for f in args:
        if not os.path.exists(f):
            raise IOError('file not found: ' + f)

    analyzer = VerilogDataflowAnalyzer(
        args,
        options.top,
        nobind=False,
        noreorder=False,
        preprocess_include=options.include,
        preprocess_define=options.define,
    )
    analyzer.generate()

    terms = analyzer.getTerms()
    binddict = analyzer.getBinddict()

    dfo = VerilogDataflowOptimizer(terms, binddict)
    dfo.resolveConstant()

    merger = VerilogDataflowMerge(
        options.top, terms, binddict,
        dfo.getResolvedTerms(), dfo.getResolvedBinddict(), dfo.getConstlist()
    )

    # Optimizer for canonicalization
    optz = VerilogOptimizer(terms, dfo.getConstlist())

    total = 0
    mismatches = 0

    for dest, blist in sorted(binddict.items(), key=lambda x: str(x[0])):
        if not blist:
            continue
        # Get optimized bind list for this dest
        opt_blist = merger.getOptimizedBindlist(tuple(blist))

        # Compare by multiset of canonical strings (order-insensitive)
        canon_raw = [_canon_bind(b, optz, do_reorder=not options.noreorder) for b in blist]
        canon_opt = [_canon_bind(b, optz, do_reorder=not options.noreorder) for b in opt_blist]

        total += 1
        sr = sorted(canon_raw)
        so = sorted(canon_opt)
        if sr != so:
            mismatches += 1
            print('== MISMATCH for %s ==' % dest)
            print('-- raw normalized --')
            for s in sr:
                print(s)
            print('-- opt normalized --')
            for s in so:
                print(s)

    if mismatches == 0:
        print('EQUIV PASS: %d signal groups matched.' % total)
    else:
        print('EQUIV FAIL: %d/%d groups mismatch.' % (mismatches, total))
        sys.exit(1)


if __name__ == '__main__':
    main()
