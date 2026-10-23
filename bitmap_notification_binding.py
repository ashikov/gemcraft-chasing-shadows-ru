"""Guard the single number-first kill-chain caption before reversing its add."""


def validate_kill_chain_prefix(before, after, old_ops, new_ops, site):
    """Allow one swap at m243@282, with no arithmetic or resource changes.

    NumberFormatter has already produced a String. Swapping that string and
    the translated caption immediately before add makes the caption a prefix;
    the stack depth, number spelling and every floating-text argument survive.
    """
    expected = {'tag_path': [4175], 'method_index': 243, 'instruction_index': 117,
                'expected_opcode': 0xa0, 'expected_operands': [],
                'kind': 'kill_chain_prefix', 'extra_stack': 0}
    if (any(site.get(key) != value for key, value in expected.items())
            or type(site.get('extra_stack')) is not int
            or 'receiver_hex' in site or 'receivers_hex' in site):
        raise ValueError('Unexpected kill-chain prefix site or resources')
    label = {'owner': 'IngameCore', 'method': 'endKillChain'}
    names = {230: 'GV', 4156: 'vfxEngine', 2133: 'NumberFormatter',
             312: 'monsterKillChainCounter', 863: 'g', 4755: 'format',
             694: 'createFloatingText'}
    for inv in (before, after):
        if (label not in inv.method_labels.get(243, [])
                or any(inv.multiname(index) != name for index, name in names.items())):
            raise ValueError('Unexpected kill-chain prefix owner or property')
    context = [
        (249, 0x60, [230]), (252, 0x66, [4156]), (255, 0x25, [778]),
        (258, 0x25, [170]), (261, 0xa0, []), (262, 0x24, [74]),
        (264, 0x60, [2133]), (267, 0xd0, []), (268, 0x66, [312]),
        (271, 0x46, [863, 0]), (275, 0x46, [4755, 1]),
        (279, 0x2c, [9585]), (282, 0xa0, []), (283, 0x2d, [206]),
        (286, 0x24, [12]), (288, 0x2c, [9586]), (291, 0x24, [0]),
        (293, 0x2f, [103]), (295, 0x24, [0]), (297, 0x2f, [24]),
        (299, 0x24, [45]), (301, 0x24, [0]), (303, 0x24, [10]),
        (305, 0x4f, [694, 13]),
    ]
    if len(old_ops) != len(new_ops) or old_ops[105:129] != context:
        raise ValueError('Unexpected kill-chain prefix expression or call context')
    translated = list(context)
    if len(new_ops) <= 128 or new_ops[116][:2] != (279, 0x2c):
        raise ValueError('Unexpected kill-chain prefix caption instruction')
    translated[11] = new_ops[116]
    if new_ops[105:129] != translated:
        raise ValueError('Unexpected translated kill-chain prefix context')
    for inv, index, value in ((before, 9585, ' kills in a row'),
                              (after, new_ops[116][2][0], 'Убийств подряд: '),
                              (before, 9586, 'right'), (after, 9586, 'right')):
        if not 0 <= index < len(inv.strings) or inv.strings[index] != value:
            raise ValueError('Unexpected kill-chain prefix caption or alignment')
