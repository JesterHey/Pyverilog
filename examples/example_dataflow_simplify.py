from __future__ import absolute_import
from __future__ import print_function

import sys
import os
from optparse import OptionParser

from pyverilog.dataflow.dataflow_analyzer import VerilogDataflowAnalyzer
from pyverilog.dataflow.optimizer import VerilogDataflowOptimizer, VerilogOptimizer
from pyverilog.dataflow.merge import VerilogDataflowMerge
from pyverilog.dataflow import reorder
from pyverilog.dataflow.dataflow import DFNode


# the next line can be removed after installation
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _collect_patterns(node, patterns):
    if node is None:
        return
    try:
        key = node.tostr()
    except Exception:
        key = str(node)
    patterns[key] = patterns.get(key, 0) + 1
    try:
        children = node.children()
    except Exception:
        children = ()
    for ch in children:
        _collect_patterns(ch, patterns)


def _apply_print_cse(tree, min_uses=2, min_chars=32):
    # Build frequency map of subtree textual representations
    freq = {}
    _collect_patterns(tree, freq)

    # Choose candidates by frequency and length
    candidates = [k for k, c in freq.items() if c >= min_uses and len(k) >= min_chars]
    # Sort by length desc to avoid partial overlaps during replacement
    candidates.sort(key=len, reverse=True)

    # Assign markers
    legend = {}
    for i, pat in enumerate(candidates, start=1):
        legend['@%d' % i] = pat

    # Render the main body by replacing occurrences with markers
    try:
        body = tree.tostr()
    except Exception:
        body = str(tree)
    for name, pat in legend.items():
        body = body.replace(pat, '[' + name + ']')

    return legend, body


def main():
    usage = 'Usage: python example_dataflow_simplify.py -t TOPMODULE file ...'
    opt = OptionParser(usage=usage)
    opt.add_option('-t', '--top', dest='top', default='TOP', help='Top module name')
    opt.add_option('-I', '--include', dest='include', action='append', default=[], help='Include path')
    opt.add_option('-D', dest='define', action='append', default=[], help='Macro definition')
    opt.add_option('--noreorder', action='store_true', dest='noreorder', default=False, help='Do not reorder dataflow before optimizing')
    opt.add_option('--nobind', action='store_true', dest='nobind', default=False, help='No binding traversal (list signals/consts)')
    opt.add_option('--pretty', action='store_true', dest='pretty', default=True, help='Enable print-only CSE pretty output (default: on)')
    opt.add_option('--cse-min-uses', dest='cse_min_uses', type='int', default=2, help='Min occurrences to factor a subtree (default: 2)')
    opt.add_option('--cse-min-chars', dest='cse_min_chars', type='int', default=48, help='Min textual length to factor a subtree (default: 48)')
    (options, args) = opt.parse_args()

    if not args:
        print(usage)
        sys.exit(1)
    for f in args:
        if not os.path.exists(f):
            raise IOError('file not found: ' + f)

    analyzer = VerilogDataflowAnalyzer(
        args,
        options.top,
        nobind=options.nobind,
        noreorder=options.noreorder,
        preprocess_include=options.include,
        preprocess_define=options.define,
    )
    analyzer.generate()

    # Echo directives and instances like the original example
    directives = analyzer.get_directives()
    print('Directive:')
    for dr in sorted(directives, key=lambda x: str(x)):
        print(dr)

    instances = analyzer.getInstances()
    print('Instance:')
    for module, instname in sorted(instances, key=lambda x: str(x[1])):
        print((module, instname))

    if options.nobind:
        print('Signal:')
        for sig in analyzer.getSignals():
            print(sig)
        print('Const:')
        for con in analyzer.getConsts():
            print(con)
        return

    # Terms and binds
    terms = analyzer.getTerms()
    binddict = analyzer.getBinddict()

    # Resolve constants (parameters/localparams) first
    dfo = VerilogDataflowOptimizer(terms, binddict)
    dfo.resolveConstant()

    # Use Merge helper to apply optimizer to each bind and merge/split ranges
    merger = VerilogDataflowMerge(
        options.top,
        terms,
        binddict,
        dfo.getResolvedTerms(),
        dfo.getResolvedBinddict(),
        dfo.getConstlist(),
    )

    print('Term:')
    for tk, tv in sorted(terms.items(), key=lambda x: str(x[0])):
        print(tv.tostr())

    print('Bind:')
    for dest, blist in sorted(binddict.items(), key=lambda x: str(x[0])):
        # Work on a reordered copy to expose more simplification opportunities
        reordered = []
        for b in blist:
            bcopy = os.__dict__  # placeholder to avoid linter warnings
            # Manual deepcopy to avoid importing copy globally; trees are immutable enough for our replace
            tree = b.tree
            if not options.noreorder and tree is not None:
                try:
                    tree = reorder.reorder(tree)
                except Exception:
                    pass
            # Rebuild a lightweight Bind-like tuple for optimization pipeline
            class _B(object):
                __slots__ = ('tree', 'dest', 'msb', 'lsb', 'ptr', 'alwaysinfo', 'parameterinfo')
                def __init__(self, src):
                    self.tree = tree
                    self.dest = src.dest
                    self.msb = src.msb
                    self.lsb = src.lsb
                    self.ptr = src.ptr
                    self.alwaysinfo = src.alwaysinfo
                    self.parameterinfo = src.parameterinfo
            reordered.append(_B(b))

        # Apply optimizer/merge on the (possibly) reordered binds
        optimized_bindlist = merger.getOptimizedBindlist(tuple(reordered)) if reordered else ()

        # Print each optimized bind, with optional print-only CSE
        for ob in optimized_bindlist:
            if options.pretty and ob.tree is not None:
                legend, body = _apply_print_cse(
                    ob.tree,
                    min_uses=options.cse_min_uses,
                    min_chars=options.cse_min_chars,
                )
                # Print legend first (local to this bind)
                if legend:
                    print('Let:')
                    for name, pat in legend.items():
                        print('  %s = %s' % (name, pat))
                # Compose a simplified Bind line
                line = '(Bind'
                if ob.dest is not None:
                    line += ' dest:' + str(ob.dest)
                if ob.msb is not None:
                    line += ' msb:' + ob.msb.tostr()
                if ob.lsb is not None:
                    line += ' lsb:' + ob.lsb.tostr()
                if ob.ptr is not None:
                    line += ' ptr:' + ob.ptr.tostr()
                line += ' tree:' + body + ')'
                print(line)
            else:
                # Fallback to default textual form
                try:
                    from pyverilog.dataflow.dataflow import Bind as _Bind
                    # Build a temporary Bind object for tostr reuse
                    tmp = _Bind(ob.tree, ob.dest, msb=ob.msb, lsb=ob.lsb, ptr=ob.ptr,
                                alwaysinfo=ob.alwaysinfo, parameterinfo=ob.parameterinfo)
                    print(tmp.tostr())
                except Exception:
                    # Minimal manual formatting
                    line = '(Bind'
                    if ob.dest is not None:
                        line += ' dest:' + str(ob.dest)
                    if ob.msb is not None:
                        line += ' msb:' + ob.msb.tostr()
                    if ob.lsb is not None:
                        line += ' lsb:' + ob.lsb.tostr()
                    if ob.ptr is not None:
                        line += ' ptr:' + ob.ptr.tostr()
                    line += ' tree:' + (ob.tree.tostr() if hasattr(ob.tree, 'tostr') else str(ob.tree)) + ')'
                    print(line)


if __name__ == '__main__':
    main()
