param([string]$Instr)
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$in=Get-Content $Instr -Raw -Encoding UTF8 | ConvertFrom-Json
$dbe=New-Object -ComObject DAO.DBEngine.120
$db=$dbe.OpenDatabase($in.dbPath)
$errors=@()
function Set-FieldProp($tdname,$fname,$pn,$pv){
  $td=$db.TableDefs($tdname); $f=$td.Fields($fname)
  try{
    $prop=$null
    try{ $prop=$f.Properties($pn) }catch{}
    if($prop -ne $null){ $prop.Value=$pv }
    else{
      $pt=10; if($pn -in "DecimalPlaces"){ $pt=3 }
      $np=$f.CreateProperty($pn,$pt,$pv); $f.Properties.Append($np)
    }
  }catch{ $script:errors+=("setProp "+$tdname+"."+$fname+"."+$pn+": "+$_.Exception.Message) }
}
foreach($a in $in.actions){
  try{
    if($a.op -eq "setFieldProp"){ Set-FieldProp $a.table $a.field $a.prop $a.value }
    elseif($a.op -eq "addField"){
      $td=$db.TableDefs($a.table)
      $size=if($null -ne $a.size){[int]$a.size}else{$null}
      $nf=$td.CreateField($a.field,[int]$a.type,$size)
      $td.Fields.Append($nf)
    }
    elseif($a.op -eq "deleteField"){ $db.TableDefs($a.table).Fields.Delete($a.field) }
    elseif($a.op -eq "setPrimary"){
      $td=$db.TableDefs($a.table)
      try{ $td.Indexes.Delete("PrimaryKey") }catch{}
      $idx=$td.CreateIndex("PrimaryKey"); $idx.Primary=$true; $idx.Unique=$true
      foreach($fn in $a.fields){
        $cf=$idx.CreateField($fn); $idx.Fields.Append($cf)
      }
      $td.Indexes.Append($idx)
    }
    elseif($a.op -eq "addRelation"){
      $attrs=0; if(-not $a.referential){ $attrs=2 }
      $rel=$db.CreateRelation($a.name,$a.table,$a.foreignTable,$attrs)
      $rf=$rel.CreateField($a.field); $rf.ForeignName=$a.foreignField; $rel.Fields.Append($rf)
      $db.Relations.Append($rel)
    }
    elseif($a.op -eq "addQuery"){
      if($null -ne ($db.QueryDefs | Where-Object { $_.Name -eq $a.name })){ $db.QueryDefs.Delete($a.name) }
      [void]$db.CreateQueryDef($a.name,$a.sql)
    }
    elseif($a.op -eq "deleteQuery"){ $db.QueryDefs.Delete($a.name) }
    elseif($a.op -eq "addRows"){
      $rs=$db.OpenRecordset($a.table)
      foreach($row in $a.rows){
        $rs.AddNew()
        foreach($p in $row.PSObject.Properties){
          if($p.Value -ne $null){ try{ $rs.Fields($p.Name).Value=$p.Value }catch{} }
        }
        $rs.Update()
      }
      $rs.Close()
    }
    elseif($a.op -eq "deleteRows"){
      $rs=$db.OpenRecordset($a.table)
      while(-not $rs.EOF){
        $match=$true
        foreach($p in $a.match.PSObject.Properties){
          if("$($rs.Fields($p.Name).Value)" -ne "$($p.Value)"){ $match=$false }
        }
        if($match){ $rs.Delete() } else { $rs.MoveNext() }
      }
      $rs.Close()
    }
    elseif($a.op -eq "copyTable"){ $db.Execute("SELECT * INTO ["+$a.dst+"] FROM ["+$a.src+"]") }
    elseif($a.op -eq "deleteTable"){ $db.Execute("DROP TABLE ["+$a.name+"]") }
  }catch{ $errors+=($a.op+": "+$_.Exception.Message) }
}
$db.Close()
[Console]::Out.Write((@{ok=($errors.Count -eq 0); errors=$errors} | ConvertTo-Json -Compress))
